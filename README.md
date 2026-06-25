# CryoRemote

![CryoRemote overview hero](assets/docs/overview-hero-1600x900.png)

CryoRemote is a UCSF ChimeraX bundle for browsing cryo-EM projects over SSH/SFTP,
with a RELION-first project browser, remote metadata preview, and cache-backed map/model opening.
Editable help and image sources live in `docs/tools/` and `assets/`; package copies are generated at build time.

<p align="center">
  <img src="assets/brand/cryoremote-wordmark-1536x512.png" alt="CryoRemote wordmark" width="720" />
</p>

## Phase 1 features

- OpenSSH alias import from `~/.ssh/config`
- Paramiko-backed SFTP browsing for direct OpenSSH targets on Windows
- RELION project detection via remote `default_pipeline.star`
- Pipeline flowchart and job table views
- Session-bound pipeline watcher for status refresh inside the active ChimeraX tool
- Remote preview for `.star`, `.txt`, `.log`, `.json`, `.out`, `.err`, `.cxc`, and MRC/MAP headers
- Local cache management for `.mrc`, `.map`, `.pdb`, `.cif`, and cached source copies of remote `.cxc`
- Remote `.cxc` execution by caching the command file locally, rewriting supported `open ...` file operands, and opening the rewritten local script in ChimeraX
- Scientific Cryo Blue asset pack generated with Agnes for branding, empty state, and in-tool icons
- RELION shortcuts for:
  - `Open Latest Refine Map`
  - `Open Last Completed Job`
  - `Open Half Maps`
  - `Open PostProcess + Model`
  - `Find In Tree`

## Current limits

- Only direct aliases are supported in phase 1
- `ProxyJump`, `ProxyCommand`, `Include`, and `Match` are warned about but not implemented
- Full map streaming is not implemented; maps and models are cached locally before opening
- Remote `.cxc` support rewrites only the leading file operands of `open ...`; nested `.cxc`, `forEachFile`, `coords`, and other option-level path semantics are not rewritten
- cryoSPARC `.cs`, Slurm integration, arbitrary remote exec, detached watchers, and queue/run controls are deferred

## Development

Install into ChimeraX 1.11:

```powershell
chimerax-console.exe --nogui --cmd "devel install . ; exit"
```

Run tests:

```powershell
python -m pytest
```

Repository layout:

- `src/` - Python bundle code
- `assets/` - brand, icon, illustration, and Agnes manifest sources
- `docs/tools/` - ChimeraX help page source

## Attribution

CryoRemote selectively borrows architectural ideas from:

- `uermel/chimerax-remotebrowser` (MIT)
- `hanjinliu/himena-relion`
- `RBVI/ChimeraX-Bundle-Template`

Visual PNG resources in `assets/` were generated for this bundle with Agnes AI image generation
and recorded in `assets/agnes/manifest.json`.

No source tree was forked wholesale. The current implementation is purpose-built for
ChimeraX + SSH + RELION remote visualization workflows.
