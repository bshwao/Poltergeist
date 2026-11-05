# Frozen S15 package bindings

These are exact copies of the two SDK wrapper packages used in the November 5
native comparison. Manifests deliberately retain this PC's absolute checkpoint
path so their frozen bytes can be verified. They are provenance artifacts, not
portable installation instructions or a new model installer.

`bindings.json` gives the package roots and ordered external-model pin paths for
S19 commit verification. Each pin contains only a SHA-256 and byte length;
weights and environment contents are not bundled. The application code for both
policies is retained in the same repository revision. Existing local runs and
registrations remain unchanged.

AIM declares the same available RGB/LiDAR rig and explicitly ignores LiDAR.
TransFuser uses trained fusion. Architecture/checkpoint differences prevent a
weights-only causal attribution. Code licenses do not grant undeclared model
redistribution rights. Publication still requires a real configured GitHub App,
a fresh verified disclosure and explicit approval; these files are not a live
check or branch-protection acceptance result.
