from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_yaml(filename):
    path = CONFIG_DIR / filename

    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {path}"
        )

    with open(path) as f:
        return yaml.safe_load(f)


def get_publisher_config():
    config = load_yaml(
        "publisher.yml"
    )

    return config["publisher"]
