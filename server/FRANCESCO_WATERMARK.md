# Francesco watermark

`francesco-watermark` è l'unico nome pubblico del metodo. Per impostazione
predefinita aggiunge alla copia PDF:

1. due QR piccoli e opachi, collocati casualmente lungo i bordi senza
   sovrapporsi al contenuto rilevato;
2. sei copie semitrasparenti e multilinea di un token testuale cifrato,
   distribuite sulla pagina senza sovrapporsi ai QR.

I tre canali crittografici sono indipendenti. QR 1, QR 2 e testo sono tre
cifrature AES-SIV separate dello stesso `secret`, ciascuna con un salt casuale
di 16 byte. I due QR hanno quindi ciphertext diversi tra loro. Le sei scritte
riusano invece lo stesso terzo ciphertext: la ridondanza serve al recupero OCR,
non genera sei identità differenti.

Entrambi i livelli sono overlay PDF nativi: il testo originale resta
selezionabile e il documento resta compatibile con livelli strutturali
applicati in precedenza.

## Installazione locale

```bash
cd server
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python -m pytest
```

La lettura del livello testuale richiede Tesseract con il language pack
inglese (`tesseract-ocr` su Debian/Ubuntu, `brew install tesseract` su macOS).
L'immagine Docker lo installa automaticamente.

Generare una chiave separata per il watermark:

```bash
python -c 'import secrets; print(secrets.token_hex(32))' > watermark-key.txt
chmod 600 watermark-key.txt
```

Applicazione locale mediante CLI:

```bash
pdfwm embed input.pdf output.pdf \
  --method francesco-watermark \
  --secret 'copy-0001' \
  --key-file watermark-key.txt
```

Testo e QR sono sempre attivi insieme. `position` rimane nella firma comune per
retrocompatibilità, ma `francesco-watermark` lo ignora e lo normalizza a valore
vuoto. Valori storici come `qr-only`, `text-only`, `no-text` e `no-qr` non
modificano l'output di questo metodo.

Verifica senza mostrare l'identificatore:

```bash
pdfwm extract output.pdf \
  --method francesco-watermark \
  --key-file watermark-key.txt
```

Per un'analisi forense autorizzata, aggiungere `--show-secret`.

## Produzione e RMAP

Configurare `.env` con:

```dotenv
RMAP_WATERMARK_METHOD=francesco-watermark
RMAP_WATERMARK_KEY=<64 caratteri esadecimali casuali>
RMAP_WATERMARK_POSITION=
```

`RMAP_WATERMARK_POSITION` resta disponibile per gli altri metodi. Quando il
metodo configurato è `francesco-watermark`, il server passa sempre `None` anche
se la variabile contiene un valore.

Quindi costruire e avviare:

```bash
docker compose config --quiet
docker compose up --build -d
docker compose exec server pdfwm methods
```

L'ultimo comando deve elencare `francesco-watermark`. RMAP genera per ogni
emissione un identificatore casuale distinto dal link di download, lo incorpora
come `secret`, registra separatamente il gruppo in `Versions.intended_for` e
salva lo SHA-256 del PDF emesso. Il gruppo non viene scritto in chiaro: viene
ricavato dal record `Versions` dopo aver autenticato e recuperato l'ID della
copia. Gli alias storici non sono supportati.

## Chiamata Python e API

```python
from watermarking_utils import apply_watermark, read_watermark

marked = apply_watermark(
    method="francesco-watermark",
    pdf="input.pdf",
    secret="copy-0001",
    key="<chiave>",
)
assert read_watermark("francesco-watermark", marked, "<chiave>") == "copy-0001"
```

Il medesimo `secret` cifrato nei tre canali viene salvato nella tabella
`Versions`, permettendo una successiva attribuzione coerente al gruppo indicato
da `intended_for`.

### Formazione del secret e record RMAP

Per ogni handshake RMAP completato, il server genera il secret con
`secrets.token_urlsafe(16)`: sono 16 byte casuali rappresentati in Base64 URL
safe senza padding, normalmente 22 caratteri. Il valore non contiene il nome
del gruppo né il link di download e cambia anche per due richieste successive
dello stesso gruppo.

Il record `Versions` conserva:

- `documentid`: documento sorgente;
- `link`: risultato/link della sessione RMAP, distinto dal secret;
- `intended_for`: identità del gruppo autenticata da RMAP;
- `secret`: identificatore casuale incorporato nel watermark;
- `method`: `francesco-watermark`;
- `path`: copia PDF emessa;
- `sha256`: digest binario SHA-256 della copia esatta emessa.

La verifica recupera il secret dal PDF e cerca il record corrispondente: il
gruppo viene attribuito tramite `intended_for`, non decifrato dal watermark.
Nell'endpoint generico `create-watermark`, invece, `secret` e `intended_for`
sono forniti dal chiamante e vengono salvati senza trasformare il secret.

## Formato e verifica del testo

Il token visibile ha forma `FWM1-LL-CIPHERTEXT`. `FWM1` identifica il formato;
`LL` codifica la lunghezza; il ciphertext usa un alfabeto esadecimale custom
composto da glifi scelti per evitare le più comuni ambiguità OCR. La stringa è
spezzata su più righe soltanto in fase di resa grafica.

`read_secret` prova prima i QR. Se non trova alcun QR valido, estrae le immagini
delle scritte, esegue OCR e converte il token nel ciphertext AES-SIV originale.
Una lettura viene accettata solo se il tag AES-SIV è valido con la chiave
configurata. È ammessa una correzione OCR limitata a un singolo inserimento,
rimozione o sostituzione: anche in questo caso decide esclusivamente
l'autenticazione crittografica, non una corrispondenza approssimata del testo.
Token assenti, chiave errata e scritte non autenticabili producono un errore;
se emergono segreti validi discordanti, il documento viene rifiutato come
ambiguo.

Di conseguenza, un documento privo dei QR può ancora essere riconosciuto come
nostro e attribuito alla copia corretta se almeno una scritta completa è
recuperabile. La suite copre sia il PDF nativo senza QR sia una conversione
raster a 300 dpi. La sola presenza grafica di `FWM1` non è mai considerata
prova.

## Limiti

Il metodo accetta segreti UTF-8 fino a 64 byte, PDF non cifrati fino a 64 MiB,
massimo 10 pagine e 16 milioni di pixel teorici per pagina a 300 dpi. QR e testo
sono autenticati: chiave errata, alterazione e identificatori validi discordanti
vengono rifiutati. Gli overlay nativi possono però essere rimossi da un
attaccante che modifica deliberatamente gli oggetti PDF. Gli altri livelli del
metodo combinato forniscono ridondanza, ma non rendono il documento immune alla
ricostruzione completa. L'OCR è un canale secondario: ritagli, compressione
aggressiva o copertura di tutte le scritte possono renderlo inconclusivo, senza
produrre un'attribuzione falsa.
