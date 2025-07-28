# utils/solana_rpc.py
import httpx
import logging

# Configure logging for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def get_sol_balance(public_key: str, rpc_url: str) -> float | None:
    """
    Fetches the SOL balance for a given public key from a Solana RPC.
    Returns balance in SOL (float) or None on failure.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [public_key]
    }
    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(rpc_url, json=payload, headers=headers, timeout=5.0)
            response.raise_for_status()
            data = response.json()
            
            if data and "result" in data and "value" in data["result"]:
                lamports = data["result"]["value"]
                # 1 SOL = 1,000,000,000 lamports
                sol_balance = lamports / 1_000_000_000
                return sol_balance
            else:
                logger.warning(f"Could not get balance for {public_key}. RPC response: {data}")
                return None
    except httpx.TimeoutException:
        logger.error(f"Timeout fetching SOL balance for {public_key} from {rpc_url}.")
        return None
    except httpx.RequestError as e:
        logger.error(f"Error fetching SOL balance for {public_key}: {e}")
        return None
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON response for SOL balance of {public_key}.")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred fetching SOL balance for {public_key}: {e}")
        return None

async def get_transaction_count(public_key: str, rpc_url: str) -> int | None:
    """
    Fetches the transaction count for a given public key from a Solana RPC.
    Note: getSignaturesForAddress is generally more useful for specific account txs,
    but getTransactionCount gets the overall ledger tx count if the public_key
    param is omitted or ignored by the RPC (which is typically for the *total* cluster TX count).
    For a specific account's transaction *history length*, `getSignaturesForAddress` is better.
    However, if the intent is to see if an address has *any* transactions, a simple `getSignaturesForAddress`
    call with `limit=1` is sufficient and what's usually implied by "transaction count" for a specific wallet.
    
    Given the `main.py`'s context of checking "Wallet Has Decent History", `getSignaturesForAddress` is more fitting.
    Let's adjust this function to use `getSignaturesForAddress` to get a count of recent transactions.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [
            public_key,
            {"limit": 100} # Fetch up to 100 recent signatures to get a sense of history
        ]
    }
    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(rpc_url, json=payload, headers=headers, timeout=5.0)
            response.raise_for_status()
            data = response.json()
            
            if data and "result" in data and isinstance(data["result"], list):
                # The length of the list of signatures is the count of recent transactions
                return len(data["result"])
            else:
                logger.warning(f"Could not get transaction count for {public_key}. RPC response: {data}")
                return 0 # Or None, depending on desired behavior for no transactions found
    except httpx.TimeoutException:
        logger.error(f"Timeout fetching transaction count for {public_key} from {rpc_url}.")
        return None
    except httpx.RequestError as e:
        logger.error(f"Error fetching transaction count for {public_key}: {e}")
        return None
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON response for transaction count of {public_key}.")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred fetching transaction count for {public_key}: {e}")
        return None
