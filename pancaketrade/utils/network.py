"""Utilities for web3 interaction."""
from json.decoder import JSONDecodeError

import requests
from loguru import logger
from pancaketrade.persistence import Abi, db
from peewee import IntegrityError
from web3.types import ChecksumAddress


class ContractABIError(Exception):
    pass


def fetch_abi(contract: ChecksumAddress, api_key: str) -> str:
    out = ''
    try:
        with db:
            abi = Abi.get(Abi.address == contract)
        out = abi.abi
    except Abi.DoesNotExist:
        r = requests.get(
            'https://api.bscscan.com/api',
            params={
                'module': 'contract',
                'action': 'getabi',
                'address': contract,
                'apikey': api_key,
            },
        )
        try:
            res = r.json()
        except JSONDecodeError:
            raise ContractABIError
        out = res['result']
        if out[0] != '[':  # abi starts with a square bracket, otherwise we got a message from bscscan
            raise ContractABIError
        try:
            db.connect()
            with db.atomic():
                Abi.create(address=contract, abi=res['result'])
        except IntegrityError:
            logger.error('Failed to create database record.')
            return ''
    return out
