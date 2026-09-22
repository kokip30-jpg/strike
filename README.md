# Strike – krytý call

Samostatná aplikace, která u sledovaných akcií hledá strike pro krytý call (expirace ~2 týdny, ~1 měsíc, ~6 týdnů).

- `index.html` – celá aplikace
- `config.json` – seznam akcií a parametry skenu
- `scripts/scan.py` – sken opcí (Alpaca) a termínů výsledků (Nasdaq), běží v GitHub Actions
- Klíče Alpaca patří do Settings → Secrets and variables → Actions jako `ALPACA_KEY` a `ALPACA_SECRET`
