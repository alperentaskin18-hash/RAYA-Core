# R.A.Y.A Core v0.1.0

R.A.Y.A Core v0.1.0 contains a local Python CLI and Tk interface for reviewing actions, running declarative skills, indexing Python projects and recording manual task handoffs. A fresh profile starts offline. Use the installation steps in README.md and run `raya capabilities` for available operations.

For a local AI demonstration, start a compatible llama.cpp server and configure its loopback port. `raya ask` shows the prompt and destination before sending. A request receipt survives restart. Use a fresh proposal after a failed or denied request; review an interrupted in-progress receipt before requesting another operation.

## Verification

The development team recorded 21 passing tests on Linux, including GUI tests on a virtual display. The suite also passed from the extracted development source package. Hosted CI success was independently confirmed for Linux and Windows on Python 3.11 and 3.12 for the reviewed development revision. All 21 test cases are retained here. One filesystem-boundary case uses an inline synthetic manifest to keep the test independent of an example file. A real CPU llama-server generated the expected two-token completion from original synthetic GGUF weights through the CLI approval workflow.

The compatibility matrix establishes software test results. Pretrained-model usefulness and latency, live GitHub metadata import, Windows speech, physical microphones, native desktop behavior and end-user filesystem ACLs still need separate validation. Synthetic provider and repository fixtures exercise API behavior. The supported worker executes five trusted built-in text operations. Native third-party plugins and automatic deployment are outside this release.

## Upgrade notes

Keep backups private. Existing receipts remain readable. Proposals created before request IDs were bound into approval digests must be issued again. On Unix, existing profiles require mode 0700. On Windows, use an owner-controlled directory with private ACLs.

## Source verification

`python tools/verify_package.py` builds the allowlisted ZIP twice, checks byte identity and file coverage, extracts it and exercises a clean CLI startup. The Python package, source inventory and SBOM identify version 0.1.0.
