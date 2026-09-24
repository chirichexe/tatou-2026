# End-to-end tests

These tests run the real stack, built from `server/Dockerfile` and `db/tatou.sql`, in Docker and use it only through its HTTP API.

The fixture sets the stack up the same way production is set up:

1. It generates test OpenPGP keys: a server keypair, two registered groups and one unregistered group.
2. It starts the stack with RMAP disabled.
3. It creates the `rmap_service` account and uploads a synthetic confidential PDF.
4. It restarts the server with RMAP enabled on that document, using `davide-watermark`.

The tests then run:

- **Delivery:** real RMAP handshakes, then `get-version`.
- **Recorded versions:** a check of the `Versions` rows in MariaDB.
- **Leak attribution:** blind reads and attacked copies, attributed through `read-watermark`. The attacks are crop plus JPEG, resize, blur, JPEG at quality 20, a page screenshot and Ghostscript `/screen`, plus two copies averaged together.
- **Negative cases:** a normal user, a wrong key, an unknown method, a missing token or CSRF header, malformed RMAP messages, a replay, a wrong nonce and an unregistered identity.

## Running

```sh
cd server
pip install -e '.[dev]'            # Python 3.12, as in the Dockerfile
pytest test_e2e                    # ~30 s once the image is cached
```

The default `pytest` run (`testpaths = test/`) does not include these tests.

## Requirements and isolation

- **Requirements:** Docker with Compose v2. Ghostscript (`gs`) is optional; without it, the Ghostscript attack is skipped.
- **Isolation from the development stack:** the stack uses its own project (`tatou-e2e`), port (`E2E_PORT`, default 5055) and subnet (`E2E_SUBNET`, default `172.28.99.0/24`). The explicit subnet also avoids "address pools fully subnetted" errors when VPN routes cover the private ranges.
- **Only test material:** generated keys, random secrets, a fake flag and a synthetic PDF.
- **Cleanup:** `docker compose down -v` runs at the end. Set `E2E_KEEP=1` to keep the stack running for debugging.
