"""Config utilities."""
import sys
from dataclasses import dataclass
from pathlib import Path
from web3.types import ChecksumAddress
from web3 import Web3

import yamale
import yaml
from loguru import logger


@dataclass
class Config:
    """Class to hold the bot configuration."""

    wallet: ChecksumAddress
    _pk: str = ''

    def __init__(self, wallet: str) -> None:
        self.wallet = Web3.toChecksumAddress(wallet)


def parse_config_file(path: Path) -> Config:
    with path.open('r') as f:
        conf = yaml.full_load(f)
    return Config(**conf)


def read_config(config_file: str) -> Config:
    config_file_path = Path(config_file)
    if not config_file_path.is_file():
        logger.error(f'Config file does not exist at {config_file_path.resolve()}')
        sys.exit(1)
    schema = yamale.make_schema(Path('schema.yml'))
    data = yamale.make_data(config_file_path)
    try:
        yamale.validate(schema, data)
    except ValueError:
        logger.exception('Config file validation failed')
        sys.exit(1)
    return parse_config_file(config_file_path)
