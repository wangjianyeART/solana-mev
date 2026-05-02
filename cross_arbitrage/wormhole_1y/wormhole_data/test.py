import requests

url = "https://solana-mainnet.core.chainstack.com/f00a5a00d7d4b4fbe9d0d2da1b79d39a"


payload = {
    "id": 1,
    "jsonrpc": "2.0",
    "method": "getAccountInfo",
    "params": [
        "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        {
            "encoding": "jsonParsed",
            "commitment": "finalized"
        }
    ]
}
headers = {"Content-Type": "application/json"}

response = requests.post(url, json=payload, headers=headers)

print(response.text)
