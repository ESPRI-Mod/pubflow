import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_config_dir():
    configured = os.environ.get(
        "PUBFLOW_CONFIG_DIR",
        str(PROJECT_ROOT / "config"),
    )
    return Path(os.path.expandvars(configured)).expanduser().resolve()


def load_yaml(filename):
    path = get_config_dir() / filename
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
    publisher = config["publisher"]
    logging = publisher.get("logging", {})
    if "directory" in logging:
        directory = Path(
            os.path.expandvars(str(logging["directory"]))
        ).expanduser()
        if not directory.is_absolute():
            directory = PROJECT_ROOT / directory
        logging["directory"] = str(directory.resolve())
    return publisher


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
    path = Path(os.path.expandvars(
        str(profiles[active]["path"])
    )).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"ESG config for profile '{active}' "
            f"does not exist: {path}"
        )
    return active, path
