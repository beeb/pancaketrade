"""Config utilities."""
import os
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

import questionary
import yamale
import yaml
from loguru import logger
from questionary import ValidationError, Validator
from web3 import Web3
from web3.types import ChecksumAddress


@dataclass
class ConfigSecrets:
    """Class to hold secrets from the config file."""

    bscscan_api_key: str
    telegram_token: str
    admin_chat_id: int
    _pk: str = field(repr=False, default='')


@dataclass
class Config:
    """Class to hold the bot configuration."""

    wallet: ChecksumAddress
    secrets: ConfigSecrets
    bsc_rpc: str = 'https://bsc-dataseed.binance.org:443'
    min_pool_size_bnb: float = 25
    monitor_interval: float = 5
    update_messages: bool = True
    config_file: str = 'config.yml'
    _pk: str = field(repr=False, default='')

    def __post_init__(self):
        self.wallet = Web3.toChecksumAddress(self.wallet)
        self.secrets = ConfigSecrets(**self.secrets, _pk=self._pk)


class PrivateKeyValidator(Validator):
    def validate(self, document):
        if len(document.text) != 64 or not all(c in string.hexdigits for c in document.text):
            raise ValidationError(message='Enter a valid private key (64 hexadecimal characters)')


def parse_config_file(path: Path) -> Config:
    with path.open('r') as f:
        conf = yaml.full_load(f)
    conf['_pk'] = os.environ.get('WALLET_PK')
    if not conf['_pk'] or len(conf['_pk']) != 64 or not all(c in string.hexdigits for c in conf['_pk']):
        conf['_pk'] = questionary.password(
            f'In order to make transactions, I need the private key for wallet {conf["wallet"]}:',
            validate=PrivateKeyValidator,
        ).ask()
    conf['config_file'] = str(path)
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
