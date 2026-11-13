# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Scope the private VST package's Compose calls to the selected component."""
from pathlib import Path
import re
import sys


MARKER = '# PERF_COMPONENT_COMPOSE_SCOPE'
FUNCTION = r'''
# PERF_COMPONENT_COMPOSE_SCOPE
perf_component_compose() {
    local -a args=()
    while (($#)); do
        case "$1" in
            -p|--project-name) [[ $# -ge 2 ]] || return 2; shift 2 ;;
            --project-name=*|-p?*) shift ;;
            -f|--file|--env-file|--project-directory|--profile|--ansi|--progress|--parallel)
                [[ $# -ge 2 ]] || return 2
                args+=("$1" "$2"); shift 2 ;;
            -*) args+=("$1"); shift ;;
            *) args+=("$@"); break ;;
        esac
    done
    docker compose --project-name "${PERF_VST_COMPONENT_PROJECT:?Component project is required}" "${args[@]}"
}
'''


def isolate(path):
    text = path.read_text()
    if MARKER in text:
        return
    if re.search(r'\bsudo\s+docker\s+compose\b', text):
        raise ValueError('VST package must invoke Docker without a sudo prefix')
    text, count = re.subn(r'\bdocker[ \t]+compose\b', 'perf_component_compose', text)
    if not count:
        raise ValueError('No Docker Compose calls found in VST deploy.sh')
    if not text.startswith('#!'):
        raise ValueError('VST deploy.sh must have a shell shebang')
    first, rest = text.split('\n', 1)
    path.write_text(first + '\n' + FUNCTION + rest)


if __name__ == '__main__':
    isolate(Path(sys.argv[1]))
