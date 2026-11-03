---
title: Guardrails Azure Policy Playground
emoji: 🛡️
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
short_description: Try Azure Policy guardrails without an Azure account
tags:
  - azure
  - terraform
  - azure-policy
  - cloud-governance
  - finops
---

# Guardrails: Azure Policy playground

Guardrails is a governed Azure landing zone built with Terraform: management groups, policy as code,
environment vending and FinOps. This Space lets you try its rules on sample resources: pick a
management group, edit a resource, and see whether Azure Policy would allow it, block it, report it or
fix its tags.

It runs the same policy JSON files the Terraform deploys, through a small Python evaluator that
models the parts of Azure Policy these rules use. Source, tests and the Terraform:
[omdkpatel810-ops/azure-guardrails](https://github.com/omdkpatel810-ops/azure-guardrails).

This Space is published from GitHub by a workflow on every change to the rules, so it always shows the
rules as they are in the repository.
