# Prequisites

- Make sure to run this at first (powershell):

```bash
 $env:PYTHONPATH="$PWD\src"
 ```

CMD:

```bash
set PYTHONPATH=%CD%\src
```


# Testing openrouter image extraction (one file)

1. Run this command:

 ```bash
python -m paperless_automation productdb extract --source "C:\Users\Anwender\iCloudDrive\Documents\Scans\2025-09-20_famila_3.jpeg"
```

# Testing openrouter full ingestion of all files in scan dir

 ```bash
python -m paperless_automation productdb ingest
```

# Init productdb database

```bash
python -m paperless_automation productdb init
```

# Running frontend

1. 

```bash
python -m paperless_automation productdb serve --host 127.0.0.1 --port 8001
```
2. 

```bash
cd .\frontend\productdb-ui\
```

3. 

```bash
npm run dev
```
