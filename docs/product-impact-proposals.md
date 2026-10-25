# Product quality and experiment-agent proposals

Status: discussed October 23 and assigned roadmap numbers October 25, 2026 at
the user's request. These now appear as S11–S19 in the
[main roadmap](scalability-plan.md), preserving S0–S10. Numbering does not claim
implementation or completion. No new external
API integration, paid usage or hosted deployment is implemented by this document.

## Proposed product milestones

| Proposal | Deliverable | Acceptance evidence |
| --- | --- | --- |
| S11 — Visual design and usability | Consistent dashboard/companion design, typography, themes, responsive charts/layouts, readable device status and complete loading/empty/error states | Visual review of every principal workflow; keyboard and web accessibility checks; first-run testing on supported desktop/display sizes |
| S12 — Guided experimentation and adoption | Guided first experiment, illustrated scenario gallery, saved experiment suites, clear model comparisons, replay highlights and useful exports | New testers complete a run and explain its result without help; deliberate feedback identifies repeated use and confusing steps |

Begin design during S5 integration; do not wait for an end-of-project styling
pass. Favor a calm simulation control room with a prominent scene/replay,
readable results, visible device status and one clear next action. Microsoft's
[Fluent design system](https://fluent2.microsoft.design/) is a reference, not a
requirement to replace the current UI stack. Use
[WCAG 2.2](https://www.w3.org/WAI/WCAG22/quickref/) as the web accessibility basis.
Usage measurement, if introduced, must be an explicit product/privacy decision;
raw experiments, local paths and recordings are not default analytics events.

## Experiment agents

The existing baseline, PlanT integration, search, reproduction, minimization,
agent comparison and evidence timeline are foundations. The additional value is
an understandable end-to-end workflow rather than renamed existing operations.

1. **S13 — Failure Discovery Agent, first agent priority.** Convert a user's evaluation goal
   into a validated, bounded experiment plan; search using approved local agents;
   repeat candidate failures; preserve unsuccessful trials; return reproducible
   scenarios and measured outcomes. The user sets execution/time budgets before
   accepting a plan. An empty search honestly reports no confirmed failure.
2. **S14 — Evidence Analyst Agent.** Summarize recorded braking, contacts and interventions
   with links to exact frames and coverage. Separate observations, hypotheses and
   missing evidence. Do not infer a model's internal reason or a causal conclusion
   merely from a collision, replay or language-model explanation.
3. **S15 — Regression Agent.** Compare frozen model versions on matched saved recipes
   and seeds. Expose sample counts, uncertainty, execution/input differences and
   incomplete results. Flag measured regressions without collapsing unknown
   coverage into a reassuring score.
4. **S16 — Sensor-based driving agents.** Add a compatible trained camera/LiDAR
   policy through the existing worker. Validate real sensor inputs, model control,
   dependencies, weights, licenses and hardware timing. The existing
   [PlanT 2.0](https://github.com/autonomousvision/plant2) object-centric integration
   remains useful but does not establish camera perception performance.

The intended demonstration is: choose a supported model, find a weakness,
reproduce it, inspect evidence, then retest an improvement. Test assistant plans
against the existing typed services; do not give an assistant arbitrary shell,
filesystem, model-installation or permission-changing operations.

## API candidates

| Connection | Proposed use | Prerequisite |
| --- | --- | --- |
| S17 — [Hugging Face Hub](https://huggingface.co/docs/huggingface_hub/guides/download) | Revision-pinned, cached model downloads for S7 | Curated supported models, verified hashes/licenses, bounded/resumable download and managed environment lifecycle |
| S18 — [OpenAI integration](https://developers.openai.com/api/docs/guides/function-calling) | Optional Discovery/Analyst language interface over typed Poltergeist functions | Explicit provider configuration and usage budget; validated tool arguments; selected/redacted evidence; independent factual/coverage checks |
| S19 — [GitHub API](https://docs.github.com/en/rest/checks/runs) | Associate regression summaries with exact model commits | Appropriate app/token permissions, explicit publishing authorization, private evidence disclosure rules and reliable status mapping |

CARLA's local Python RPC is already used for simulation. Additional provider
calls stay outside the frame-by-frame driving loop. First integrate managed model
downloads, then an optional experiment assistant; CI reporting can follow a
validated regression workflow. Account/device authorization and remote dispatch
still depend on S4–S6. The existing trusted local API is not a hosted integration
endpoint.
