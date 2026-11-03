# Guardrails

**A governed Azure landing zone, built with Terraform: rules that stop the expensive mistakes before they happen.**

[![CI](https://github.com/omdkpatel810-ops/azure-guardrails/actions/workflows/ci.yml/badge.svg)](https://github.com/omdkpatel810-ops/azure-guardrails/actions/workflows/ci.yml)

> **Status: milestone 1 of 4 is built and tested offline.** The management groups, the policies and the
> pipeline are done: 22 Terraform tests run against mocked Azure providers, and 187 Python tests check
> the rules themselves. The first real deployment waits on the Azure subscription, and its measured
> results will go here.

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

## What milestone 1 builds

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

## Security choices

- **No secrets anywhere.** GitHub Actions signs in to Azure with OpenID Connect. Each run gets a
  short-lived token, and Entra ID trusts it only for the subjects [`bootstrap/`](bootstrap/main.tf) lists.
- **Two identities.** `guardrails-plan` can only read. Pull requests and the nightly drift check use it.
  `guardrails-apply` can change things, but only from `main` through the `production` environment.
- **The pipeline can't make anyone Owner.** It assigns roles through Role Based Access Control
  Administrator with a condition: only Tag Contributor, and only to service principals (the policies'
  own identities).
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
| Each rule against resources that should and shouldn't pass | pytest, via the evaluator | 187 Python tests, 100% coverage |
| The policy files: names, parameters, references, roles | pytest | (included above) |
| `bootstrap.sh`: first run, re-run, region limits, GitHub fallbacks | pytest, with stand-in `az`, `terraform` and `gh` | (included above) |
| The Hugging Face Space: every example, bad input, the page, the published folder | pytest, with Gradio | (included above) |
| The hierarchy, assignments, identities, exemption rules | `terraform test` with mocked providers | 22 |
| Formatting, validation, linting, security scanning | `terraform fmt` and `validate`, tflint, Checkov, ruff, shellcheck | every push |

Checkov passes 15 checks on the bootstrap. It skips 7, each with its reason written next to it in
[`bootstrap/main.tf`](bootstrap/main.tf), and passes all 152 checks on the GitHub workflows.

Run everything locally:

```bash
pip install -r requirements-dev.txt
pytest --cov
for d in platform bootstrap modules/management-groups modules/policy; do
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
3. Applies `bootstrap/`, then moves bootstrap's own state into the storage it just created.
4. Applies `platform/` once, as you. This creates the hierarchy and policies and places the
   subscription. Moving a subscription needs Owner, which the pipeline deliberately doesn't have.
5. Sets the repository variables and the `production` environment, or prints them for you to add.

After that, every change is a pull request: the plan shows in the pull request, and merging applies it.
A nightly plan fails if anyone changed the managed policies or management groups outside the pipeline.

**Cost.** Management groups and Azure Policy are free. State storage is a few cents a month. The audit
log's daily cap of 0.05 GB keeps even a runaway month to a few dollars, and normal use is a tiny fraction
of that.

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
2. **Environment vending:** a pull request creates a resource group with an owner, a budget and access
3. **FinOps:** budgets, cost exports and a cost dashboard by team
4. **Seed the mess:** create untagged VMs and public storage, find and fix them, and measure how long it takes

## Layout

```
policies/            the rules, as JSON: definitions, initiatives, assignments
modules/             management-groups and policy Terraform modules, each with tests/
platform/            the root configuration the pipeline applies
bootstrap/           state storage, pipeline identities, and bootstrap.sh
tools/policy_eval.py the offline policy evaluator
examples/            sample resources for trying the rules
space/               the Hugging Face Space (Gradio), built by scripts/build_space.py
tests/               Python tests
```
