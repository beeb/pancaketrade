# PancakeTrade - Limit orders and more for PancakeSwap on Binance Smart Chain

PancakeTrade helps you create limit orders and more for your BEP-20 tokens that swap against BNB on PancakeSwap. The bot is controlled by Telegram so you can interact from anywhere.

## DISCLAIMER

This software is for educational purposes only. Do not risk money which you are afraid to lose. USE THE SOFTWARE AT YOUR OWN RISK. THE AUTHORS AND ALL AFFILIATES ASSUME NO RESPONSIBILITY FOR YOUR TRADING RESULTS.

We strongly recommend you to have coding and Python knowledge. Do not hesitate to read the source code and understand the mechanism of this bot.

## Requirements

In order to use this bot, you will need the following:

- A server or computer that can run the script continuously (linux or macOS)
- Python version between 3.7.1 and 3.9.x
- [Poetry](https://github.com/python-poetry/poetry)
- A BscScan API key (create an account on [BscScan](https://bscscan.com/) then visit [My API Keys](https://bscscan.com/myapikey))
- A Telegram bot token (interact with [@BotFather](https://telegram.me/BotFather))
- Your Telegram chat ID/user ID (interact with [@userinfobot](https://telegram.me/userinfobot))
- Optional: `git` to clone this repository, you can also download the source from the github website.

## Quick Start

Before you start, make sure you have your BscScan API key and Telegram Bot token + user ID available.
Initiate the chat with your bot on Telegram (click the "Start" button) to initialize the chat ID before you start the bot the first time.

Run the following commands

```bash
git clone https://github.com/beeb/pancaketrade.git
cd pancaketrade
poetry install --no-dev
cp config.example.yml config.yml
```

Next, open the `config.yml` file with a text editor and populate the `wallet` and `secrets` section.

The bot needs your wallet's private key in order to sign and execute orders on your behalf. You can either run the
command below and enter your private key in the prompt that will be shown, or you can provide an environment variable
named `WALLET_PK` that will be used by the bot.

Run the bot:

```bash
poetry run trade
```

## Run as a service

On systems that support `systemd`, you can use the included `pancaketrade.service` file to run this script as a service.

```bash
cp pancaketrade.service ~/.config/systemd/user/
# edit the new file in .config/systemd/user with your wallet private key
systemctl --user start pancaketrade.service
systemctl --user enable pancaketrade.service # run at launch
```

## Donations

If you feel like this project has helped you and you wish to donate to the developer, you can do so on the Ethereum or Binance Smart Chain networks at the address:

`0x026E539B566DcFF02af980d938deCcb11255d519`

Thanks for your contribution!
