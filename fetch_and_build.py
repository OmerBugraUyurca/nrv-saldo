import requests
import pandas as pd
import json
import os
from io import StringIO
from datetime import date, timedelta
from collections import defaultdict

CLIENT_ID     = os.environ["NTZ_CLIENT_ID"]
CLIENT_SECRET = os.environ["NTZ_CLIENT_SECRET"]

# ---------- 1. Token ----------
def get_token():
    r = requests.post(
        "https://identity.netztransparenz.de/users/connect/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID.strip(),
            "client_secret": CLIENT_SECRET.strip(),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    r.raise_for_status()
    return r.json()["access_token"]

# ---------- 2. Veri çek ----------
def fetch_nrv(date_from, date_to, token):
    r = requests.get(
        f"https://ds.netztransparenz.de/api/v1/data/NrvSaldo/NRVSaldo/Betrieblich/{date_from}/{date_to}",
        headers={"Authorization": f"Bearer {token}"}
    )
    r.raise_for_status()
    return pd.read_csv(StringIO(r.text), sep=";", decimal=",")

# ---------- 3. CSV güncelle ----------
def update_csv(new_df):
    path = "data/nrv_saldo.csv"
    os.makedirs("data", exist_ok=True)
    if os.path.exists(path):
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_df]).drop_duplicates(
            subset=["Datum","von","bis"]
        )
    else:
        combined = new_df
    combined.to_csv(path, index=False)
    print(f"CSV: {len(combined)} satır")
    return combined

# ---------- 4. JSON üret (heatmap için) ----------
def build_json(df):
    os.makedirs("data", exist_ok=True)

    # Datum DD.MM.YYYY → parse
    df = df.copy()
    df["date_parsed"] = pd.to_datetime(df["Datum"], dayfirst=True)
    df["month"]  = df["date_parsed"].dt.month
    df["year"]   = df["date_parsed"].dt.year
    df["day"]    = df["date_parsed"].dt.day

    def quarter(von):
        mins = von.split(":")[1]
        return {"00":"Q1","15":"Q2","30":"Q3","45":"Q4"}.get(mins, "Q1")

    df["quarter"] = df["von"].apply(quarter)
    df["hour"]    = df["von"].apply(lambda x: int(x.split(":")[0]))
    df["key"]     = df["hour"].apply(lambda h: f"{h:02d}") + "-" + df["quarter"]

    # Her ay için ayrı JSON
    result = {}
    for (year, month), grp in df.groupby(["year","month"]):
        label = f"{year}-{month:02d}"
        data = defaultdict(dict)
        for _, row in grp.iterrows():
            val = row["Deutschland"]
            if pd.notna(val):
                data[row["key"]][str(row["day"])] = round(float(val), 1)
        result[label] = dict(data)

    with open("data/nrv_data.json", "w") as f:
        json.dump(result, f)
    print(f"JSON: {len(result)} ay")
    return result

# ---------- 5. HTML üret ----------
def build_html(result):
    months = sorted(result.keys())
    latest = months[-1] if months else ""

    html = open("template.html").read()
    html = html.replace("__DATA_JSON__", json.dumps(result))
    html = html.replace("__LATEST_MONTH__", latest)
    html = html.replace("__UPDATED_AT__", date.today().strftime("%Y-%m-%d"))

    with open("index.html", "w") as f:
        f.write(html)
    print(f"HTML oluşturuldu — son ay: {latest}")

# ---------- MAIN ----------
if __name__ == "__main__":
    token = get_token()
    print("Token OK")

    # Son 3 günü çek (overlap için)
    today     = date.today()
    date_from = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    date_to   = today.strftime("%Y-%m-%d")

    new_df = fetch_nrv(date_from, date_to, token)
    print(f"Çekilen: {len(new_df)} satır")

    df = update_csv(new_df)
    result = build_json(df)
    build_html(result)
    print("Tamamlandı.")
