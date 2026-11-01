# What DREAD needs in the home (claude-container's `devtools setup dread`; again is harmless): its
# venv, with the extras the tests and CI use. The MSP430 compiler (TI's GCC, for
# gizmo-msp430) is unpacked in ~/.local/opt with wrappers in ~/.local/bin, not a package.
set -eu
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install --quiet -e ".[test,prove,dwarf]"
