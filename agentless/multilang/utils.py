import json
from pathlib import Path

from agentless.multilang.const import LANGUAGE, LANG_EXT


def process(raw_data):
    raw = json.loads(raw_data)
    data = {
        'repo': f'{raw["org"]}/{raw["repo"]}',
        'instance_id': raw['instance_id'],
        'base_commit': raw['base']['sha'],
        'problem_statement': raw['resolved_issues'][0]['title'] + '\n' + raw['resolved_issues'][0]['body'],
    }
    return data


def load_local_json():
    dataset = []
    if LANGUAGE == 'javascript':
        lang = 'js'
    elif LANGUAGE == 'typescript':
        lang = 'ts'
    else:
        lang = LANGUAGE
    path = Path(f'data/{lang}')
    lines = []
    for file in path.iterdir():
        if not file.name.endswith(".jsonl"):
            continue
        lines.extend(file.read_text().splitlines())
    dataset = [process(x) for x in lines]
    return dataset


def end_with_ext(file_name):
    # OpenFOAM and some C/C++ repos often use uppercase extensions (.C/.H).
    # Match extensions case-insensitively to avoid dropping valid source files.
    file_name_lower = file_name.lower()
    for ext in LANG_EXT:
        if file_name_lower.endswith(f".{ext.lower()}"):
            return True
    return False
