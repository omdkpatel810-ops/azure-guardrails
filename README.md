# Guardrails

**A governed Azure landing zone, built with Terraform: rules that stop the expensive mistakes before they happen.**

[![CI](https://github.com/omdkpatel810-ops/azure-guardrails/actions/workflows/ci.yml/badge.svg)](https://github.com/omdkpatel810-ops/azure-guardrails/actions/workflows/ci.yml)

> **Status: all four milestones are built and tested offline.** The management groups, the policies,
> environment vending, the FinOps report, the mess drill and the pipelines are done: 38 Terraform tests
> run against mocked Azure providers, and 282 Python tests check the rules, the requests, the report and
> the drill. The first real deployment waits on the Azure subscription; the drill's measured results
> will go in [Results](#results).

## The problem

A company of a few hundred people moves to Azure. Each team gets a subscription, and within a year:

- **Customer data leaves Canada.** Someone picks East US because it's a few cents cheaper, and the
  company now has a privacy problem with its Canadian customers.
- **Nobody owns half the bill.** Untagged resources with names like `vm-test-2` cost thousands a month,
  and finance can't say which team to ask.
- **A storage account is left open to the internet.** Publicly readable storage is one of the most common
  causes of cloud data leaks.

Writing a wiki page about it doesn't help. Guardrails turns the rules into code that Azure enforces on every
request, reviews every change to them in a pull request, and makes every exception expire.

## Milestone 1: rules as code

A management group hierarchy following Microsoft's Cloud Adoption Framework, with a policy at each level:

```
Guardrails (root)               data residency: Canada Central and Canada East only
├── Platform                    baseline
├── Landing zones               baseline
│   ├── Corp                    + no public IP addresses (internal apps)
│   └── Online                  (internet-facing apps)
├── Sandbox                     baseline in audit mode: reported, not blocked
└── Decommissioned              clean-up only: nothing new can be created
```

Policies set at a level apply to everything below it, so data residency covers the sandbox too.

| Rule | What it does | Effect |
|---|---|---|
| Allowed regions | Resources and resource groups only in Canada Central or Canada East (global services excepted) | Deny |
| Required tags | Every resource group has `owner`, `cost-center` and `environment` | Deny (Audit in the sandbox) |
| Tag inheritance | A resource missing one of those tags gets it from its resource group | Modify |
| No public blobs | Storage accounts can't allow anonymous blob access | Deny (Audit in the sandbox) |
| Secure transfer | Storage accounts accept HTTPS only, with TLS 1.2 or newer | Deny (Audit in the sandbox) |
| No public IPs | Corp workloads are reached only over the private network | Deny |
| Decommissioned | Only locks, role assignments and policy can change, so a retired subscription can only be cleaned up | Deny |

Each rule is a JSON file in [`policies/definitions`](policies/definitions), grouped into two initiatives
in [`policies/initiatives`](policies/initiatives), and assigned in
[`policies/assignments.json`](policies/assignments.json). Terraform deploys exactly those files, and the
Python tests read the same files, so what's tested is what's deployed.

## Try the rules without Azure

**In the browser:** the [Guardrails playground](https://huggingface.co/spaces/Om1627/azure-guardrails) on Hugging Face
lets you pick a management group, edit a resource, and see what Azure Policy would do with it. A workflow
publishes it from this repo every time CI passes on `main`, so it always runs the rules as they are here.

**On the command line:** [`tools/policy_eval.py`](tools/policy_eval.py) is a small Azure Policy evaluator. Given a resource and a
management group, it applies every assignment from the root down and says what Azure would do:

```console
$ python tools/policy_eval.py online examples/rg-untagged.json
Deny     landing-zones/gr-baseline    require-resource-group-tag (rg-needs-owner)
Deny     landing-zones/gr-baseline    require-resource-group-tag (rg-needs-cost-center)
Deny     landing-zones/gr-baseline    require-resource-group-tag (rg-needs-environment)
DENIED

$ python tools/policy_eval.py sandbox examples/rg-untagged.json
Audit    sandbox/gr-baseline-audit    require-resource-group-tag (rg-needs-owner)
Audit    sandbox/gr-baseline-audit    require-resource-group-tag (rg-needs-cost-center)
Audit    sandbox/gr-baseline-audit    require-resource-group-tag (rg-needs-environment)
ALLOWED

$ python tools/policy_eval.py online examples/vm-untagged.json --resource-group examples/rg-tagged.json
Modify   landing-zones/gr-baseline    inherit-resource-group-tag (inherit-owner)
Modify   landing-zones/gr-baseline    inherit-resource-group-tag (inherit-cost-center)
Modify   landing-zones/gr-baseline    inherit-resource-group-tag (inherit-environment)
tags after Modify: {"owner": "payments-team@example.com", "cost-center": "cc-2040", "environment": "dev"}
ALLOWED

$ python tools/policy_eval.py sandbox examples/vm-us-region.json
Deny     root/gr-locations            allowed-locations (resources-in-allowed-regions)
DENIED
```

It models the parts of Azure's behaviour these rules depend on: string comparisons ignore case, Indexed
mode skips resource groups, and Modify runs before Deny and Audit. It is a model, though, not Azure: the
deployment's measured results are the real test.

## Milestone 2: environments by pull request

A team asks for an environment by adding one file to [`environments/`](environments):

```json
{
  "team": "payments",
  "environment": "dev",
  "owner": "payments-lead@example.com",
  "cost_center": "cc-2040",
  "region": "canadacentral",
  "monthly_budget": 150,
  "access": { "contributors": ["grp-guardrails-payments"], "readers": ["grp-guardrails-finance"] },
  "address_space": "10.100.1.0/24"
}
```

The pull request runs [`tools/vending.py`](tools/vending.py), which checks what Terraform can't:

- the file is named after its team and environment;
- the budget is within the limit for the environment (500 a month for dev and test, 5,000 for prod);
- no two networks overlap, and each sits inside `10.100.0.0/16`;
- the resource group it creates passes every guardrail in its management group, using the same evaluator.

Then it plans `vending/` and shows the request as a table. Merging creates, through
[`modules/environment`](modules/environment/main.tf):

| What | Detail |
|---|---|
| Resource group | `rg-<team>-<environment>`, tagged with owner, cost centre, environment, team and the request file |
| Budget | Monthly, with email alerts to the owner at 50%, 80% and 100% of actual spend, and at 100% forecast |
| Access | The team's Entra ID group gets Contributor in dev and test, and only Reader in prod |
| Network (optional) | A virtual network, a workloads subnet with no default internet access, and a network security group on it |
| Prod lock | Prod resource groups can't be deleted until the request is removed in a reviewed pull request |

Seven requests from five pretend teams (payments, storefront, data, support and marketing) are in
[`environments/`](environments), and bootstrap creates their groups. The eighth is real:
[OneView](https://github.com/omdkpatel810-ops/azure-oneview), the next project, gets its resource group
the same way.

## Milestone 3: FinOps

Bootstrap sets up the data, once:

- a **daily export** of the month's actual costs to storage, kept for 13 months;
- an **anomaly alert**: Cost Management emails when spending breaks its usual daily pattern;
- the subscription's **activity log** in the audit workspace, so every change has a who and a when.

Then [`finops/report.py`](finops/report.py) turns the newest export, an
[Azure Resource Graph inventory](finops/queries/resources.kql) and Azure Advisor's cost recommendations
into one report. A [workflow](.github/workflows/finops.yml) builds it every morning, signed in as the
read-only identity. The report shows:

| Section | What it answers |
|---|---|
| Spend with an owner | The headline measure: the share of spend on resources with an owner tag. The target is 100%. |
| Spend by team | By team tag; failing that, matched to a team through its cost centre or resource group in `environments/` |
| Budgets | Each vended environment's spend and month-end forecast against its budget: On track, Watch (over 80%) or Over budget |
| Spend with no owner | Each untagged resource and what it has cost this month |
| Savings found | Idle resources (VMs stopped but still allocated, unattached disks, orphaned public IPs, App Service plans with no apps), plus Advisor's rightsizing, with what each costs a month |

It reads every export format Azure produces: pay-as-you-go, Microsoft Customer Agreement and FOCUS
columns, including older exports' tags without braces.

**Try it without Azure.** [`finops/sample.py`](finops/sample.py) writes a month of made-up data in
Azure's formats: the five teams, the platform, and a forgotten proof of concept with two untagged VMs,
orphaned disks and IPs, and a Premium database that pushes the data team over budget. The Hugging Face
Space shows the report on it, or:

```bash
python -m finops.sample build/sample
python -m finops.report --costs build/sample/costs.csv --resources build/sample/resources.json \
  --advisor build/sample/advisor.json --html build/report.html
```

## Milestone 4: the mess drill

Guardrails should find a mess and help fix it, and the drill puts a number on that. In Cloud Shell, as
the subscription's owner:

```bash
python drills/drill.py seed --yes    # about $0.30 an hour while the mess exists
python drills/drill.py measure       # start the clock
python drills/drill.py clean --yes
```

**Seed.** [`drills/mess`](drills/mess/main.tf) creates a forgotten proof of concept:
- two VMs left running, with no tags;
- a storage account that allows anonymous reads;
- a database at S3 that would be fine at S0;
- a disk, a public IP and an App Service plan that nobody uses.

A 48-hour exemption, with an owner and a reason like any other, lets it be created under the guardrails,
as if it predated them.

**Find.** `measure` removes the exemption and starts the clock. It asks Azure Policy to scan, and waits
until all 11 resources are reported non-compliant.

**Fix.** Remediation tasks copy the three missing tags from the resource group. Terraform closes the
storage, deletes the idle disk, IP and plan, and shrinks the database. The clock stops when the resource
group is fully compliant.

**Measure.** It saves the time to find, the time to fix and what broke which rule. It also prices what
the clean-up removed with [Azure's public retail prices](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices).
Everything goes to `drills/results/`.

[`tests/test_drill.py`](tests/test_drill.py) runs the whole sequence against a stand-in shell and clock,
including a drill that runs out of time.

## Results

Pending the first deployment. Each drill adds its table here.

## Security choices

- **No secrets anywhere.** GitHub Actions signs in to Azure with OpenID Connect. Each run gets a
  short-lived token, and Entra ID trusts it only for the subjects [`bootstrap/`](bootstrap/main.tf) lists.
- **Two identities.** `guardrails-plan` can only read. Pull requests and the nightly drift check use it.
  `guardrails-apply` can change things, but only from `main` through the `production` environment.
- **The pipeline can't make anyone Owner.** It assigns roles through Role Based Access Control
  Administrator with a condition: Tag Contributor only to service principals (the policies' own
  identities), and Contributor or Reader only to groups (vended environments). Owner, User Access
  Administrator and RBAC Administrator are never assignable.
- **Vending can only build environments.** Instead of Contributor, the pipeline gets a custom role
  that can create resource groups, networks, budgets and delete locks, and nothing else: no VMs,
  databases or storage. In Microsoft Graph it can only read groups.
- **Least privilege for the policies, too.** Microsoft's built-in tag inheritance policy asks for
  Contributor. Tag Contributor is enough to copy a tag, so that's what these ask for. Planning fails if a
  policy ever asks for a role outside `allowed_remediation_roles`.
- **Exceptions expire.** An exemption needs an owner's email, a reason and an expiry within 90 days.
  Terraform rejects anything else before it reaches Azure, and renewing one means another reviewed pull
  request.
- **State is protected.** Terraform state is kept in zone-redundant storage with a copy in Canada East.
  Account keys are off, so only Entra ID sign-in works. Versioning and 30-day soft delete undo a bad
  write, a delete lock and `prevent_destroy` guard the account, and every read is logged to Log Analytics.

## Tests

| What | How | Count |
|---|---|---|
| Each rule against resources that should and shouldn't pass | pytest, via the evaluator | 282 Python tests, 100% coverage |
| The policy files: names, parameters, references, roles | pytest | (included above) |
| `bootstrap.sh`: first run, re-run, region limits, GitHub fallbacks | pytest, with stand-in `az`, `terraform` and `gh` | (included above) |
| The Hugging Face Space: every example, bad input, the page, the published folder | pytest, with Gradio | (included above) |
| Environment requests: every kind of mistake, overlapping networks, the guardrails | pytest | (included above) |
| FinOps: three export formats, each idle rule, Advisor, attribution, budgets, the report | pytest | (included above) |
| The mess drill: step order, timing, timeouts, prices | pytest, with a stand-in shell and clock | (included above) |
| The hierarchy, assignments, identities, exemption rules, vended environments, pipeline permissions, cost exports, the mess | `terraform test` with mocked providers | 38 |
| Formatting, validation, linting, security scanning | `terraform fmt` and `validate`, tflint, Checkov, ruff, shellcheck | every push |

Checkov passes 56 checks on the Terraform and skips 44, each with its reason written next to it: 16 in
[`bootstrap/`](bootstrap), and 28 in [`drills/mess`](drills/mess/main.tf), which is insecure on purpose.
It passes all 260 checks on the GitHub workflows.

Run everything locally:

```bash
pip install -r requirements-dev.txt
pytest --cov
for d in platform vending bootstrap modules/* drills/mess; do
  (cd $d && terraform init -backend=false && terraform test)
done
```

## Deploying it

You need an Azure subscription in a directory where you're an administrator (your own, not your school's
or employer's), because creating management groups and app registrations needs that. Then, once, in
[Azure Cloud Shell](https://shell.azure.com) (Bash):

```bash
git clone https://github.com/omdkpatel810-ops/azure-guardrails.git
cd azure-guardrails
gh auth login                       # optional: lets the script set the GitHub variables for you
./bootstrap/bootstrap.sh online     # the management group this subscription goes in
```

The script shows each plan and waits for you to type `yes`. It:

1. Checks the subscription allows Canada Central. Some offers, Azure for Students among them, limit
   regions. If yours does, the script lists the allowed ones and stops.
2. Creates the Guardrails root management group.
3. Applies `bootstrap/`: state storage, the two pipeline identities and their roles, and a group for each
   demo team. Granting the pipelines read access to groups in Microsoft Graph needs an Entra ID admin,
   which you are in your own directory. Then it moves bootstrap's own state into the new storage.
4. Applies `platform/` once, as you. This creates the hierarchy and policies and places the
   subscription. Moving a subscription needs Owner, which the pipeline deliberately doesn't have.
5. Sets the repository variables and the `production` environment, or prints them for you to add.

After that, every change is a pull request: the plan shows in the pull request, and merging applies it.
A nightly plan fails if anyone changed the managed policies or management groups outside the pipeline.

**Cost.** Management groups, Azure Policy, budgets, anomaly alerts and 90 days of activity log are free. State storage and cost exports are a few cents a month. The audit log's daily cap of 0.05 GB keeps even a runaway month to a few dollars, and normal use is a tiny fraction of that.

## Design decisions

- **Audit in the sandbox, not off.** Experiments shouldn't be blocked, but a sandbox full of public
  storage is still worth knowing about. Data residency stays Deny everywhere: no experiment needs
  customer data in another country.
- **Effects are parameters.** The same definition denies in landing zones and audits in the sandbox.
  If a rule misfires in production, it can be set to `Disabled` with a one-line change, not rewritten in
  a hurry.
- **Enforcement stays on.** Audit mode comes from the effect, never from `enforce = false`. That way the
  assignment always reports compliance, and nothing is switched off without anyone noticing.
- **Plans don't lock state.** The plan identity can't write, so it plans with `-lock=false`. Only applies
  lock, and runs in this workflow never overlap.

## Roadmap

1. **Management groups and policy as code** ✅ built and tested offline (deployment pending)
2. **Environment vending** ✅ built and tested offline (deployment pending)
3. **FinOps** ✅ built and tested offline (deployment pending)
4. **The mess drill** ✅ built and tested offline (deployment pending)

## Layout

```
policies/            the rules, as JSON: definitions, initiatives, assignments
modules/             management-groups, policy and environment Terraform modules, each with tests/
platform/            management groups and policy: the first stack the pipeline applies
environments/        one request per vended environment
vending/             builds every request: the second stack
tools/vending.py     checks requests and summarises them for pull requests
finops/              the FinOps report: export reader, idle resources, Advisor, sample data
drills/              the mess drill: the mess in Terraform, and drill.py to seed, measure and clean
bootstrap/           state storage, pipeline identities, and bootstrap.sh
tools/policy_eval.py the offline policy evaluator
examples/            sample resources for trying the rules
space/               the Hugging Face Space (Gradio), built by scripts/build_space.py
tests/               Python tests
```
