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


def get_active_esg_config():
    config = get_publisher_config()
    esg_config = config["esg"]["config"]
    active = esg_config["active"]
    profiles = esg_config["profiles"]
    if active not in profiles:
        raise ValueError(
            f"Unknown ESG config profile '{active}'. "
            f"Available profiles: "
            f"{', '.join(profiles)}"
        )
    path = Path(
        profiles[active]["path"]
    ).expanduser()
    if not path.is_file():
        raise FileNotFoundError(
            f"ESG config for profile '{active}' "
            f"does not exist: {path}"
        )
    return active, path