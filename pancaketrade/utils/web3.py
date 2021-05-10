"""Utilities for web3 interaction."""
from cachetools import cached, LRUCache
import requests
from web3.types import ChecksumAddress


@cached(cache=LRUCache(maxsize=256))
def fetch_abi(contract: ChecksumAddress, api_key: str) -> str:
    r = requests.get(
        'https://api.bscscan.com/api',
        params={
            'module': 'contract',
            'action': 'getabi',
            'address': contract,
            'apikey': api_key,
        },
    )
    res = r.json()
    return res['result']
