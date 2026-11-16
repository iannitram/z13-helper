# Version-pinned compute userspace (proposed)

Status: proposed, waiting for the owner's decision. Nothing in this ADR is built.

Issue #26 adds a compute-health panel that reports the host's ROCm and Vulkan state and runs a bounded allocation test. The panel only reports what the host has. SteamOS updates replace the root image, so a hand-installed ROCm under `/opt` can disappear or drift, and ROCm support for gfx1151 varies by component and release. The question is whether Z13-Helper should also offer a pinned userspace that survives those updates.

## Constraints already decided

- No `HSA_OVERRIDE_GFX_VERSION`: it makes the runtime claim a different GPU than the one installed.
- No global GTT or kernel-parameter changes from this app.
- No package installs by guesswork; any install step must name an exact version and source.
- The panel stays read-only and the allocation test stays on request, inside its stated budget.

## Options to choose between

- **Distrobox or Podman container with a pinned ROCm image.** Survives SteamOS updates, keeps the host untouched, needs a container runtime on the device and a way to pass `/dev/kfd` and render nodes in. The panel would have to discover the container's runtime as well as the host's.
- **Nix or Flatpak runtime extension.** Same isolation, different tooling; neither is known to carry a gfx1151-capable ROCm today.
- **Do nothing.** The panel reports what the host has and says what is missing. Smallest surface.

## Questions for the owner

1. Is a pinned environment in scope for Z13-Helper at all, or is the diagnostic enough?
2. If yes, which container route, and which ROCm version is the pin?
3. Who updates the pin, and how is a bad pin rolled back?

Until this is decided the panel's compatibility rows are a hint (see `COMPAT_NOTES` in `src/compute_health.py`), not a promise.
