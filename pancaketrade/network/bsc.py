from web3 import Web3


class Network:
    def __init__(self):
        w3_provider = Web3.HTTPProvider(endpoint_uri='https://bsc-dataseed1.binance.org:443')
        self.w3 = Web3(provider=w3_provider)
