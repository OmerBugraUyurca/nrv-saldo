import requests
import pandas as pd
import json
import os
from io import StringIO
from datetime import date, timedelta
from collections import defaultdict
from dateutil.relativedelta import relativedelta

CLIENT_ID     = os.environ["NTZ_CLIENT_ID"]
CLIENT_SECRET = os.environ["NTZ_CLIENT_SECRET"]
EQ_API_KEY    = os.environ.get("EQ_API_KEY", "")

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

# ---------- 2. Netztransparenz cekiciler ----------
def fetch_ntz(endpoint, date_from, date_to, token):
    r = requests.get(
        f"https://ds.netztransparenz.de/api/v1/data/{endpoint}/{date_from}/{date_to}",
        headers={"Authorization": f"Bearer {token}"}
    )
    r.raise_for_status()
    return pd.read_csv(StringIO(r.text), sep=";", decimal=",")

def fetch_nrv(date_from, date_to, token):
    return fetch_ntz("NrvSaldo/NRVSaldo/Betrieblich", date_from, date_to, token)

def fetch_aep(date_from, date_to, token):
    return fetch_ntz("NrvSaldo/AepSchaetzer/Betrieblich", date_from, date_to, token)

# ---------- 3. CSV birlestirici ----------
def update_csv(new_df, path):
    os.makedirs("data", exist_ok=True)
    if os.path.exists(path) and len(new_df):
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_df]).drop_duplicates(
            subset=["Datum","von","bis"], keep="last"
        )
    elif len(new_df):
        combined = new_df
    elif os.path.exists(path):
        combined = pd.read_csv(path)
    else:
        combined = pd.DataFrame()
    if len(combined):
        combined.to_csv(path, index=False)
    print(f"{path}: {len(combined)} satir")
    return combined

# ---------- 4. NTZ df -> {date: {HH-Qx: val}} ----------
def df_to_map(df, value_col):
    if not len(df):
        return {}
    df = df.copy()
    dt_str = df["Datum"] + " " + df["von"]
    ts_utc = pd.to_datetime(dt_str, format="%d.%m.%Y %H:%M", utc=True)
    ts_local = ts_utc.dt.tz_convert("Europe/Berlin")
    df["val"]      = pd.to_numeric(df[value_col], errors="coerce")
    df["date_str"] = ts_local.dt.strftime("%Y-%m-%d")
    df["hour"]     = ts_local.dt.hour
    df["minute"]   = ts_local.dt.minute
    df["key"] = df.apply(lambda r: f"{int(r['hour']):02d}-Q{int(r['minute'])//15+1}", axis=1)
    result = defaultdict(dict)
    for _, row in df.iterrows():
        if pd.notna(row["val"]):
            result[row["date_str"]][row["key"]] = round(float(row["val"]), 2)
    return dict(sorted(result.items()))

def find_value_col(df, prefer_substrings):
    meta = {"Datum","Zeitzone","von","bis","Datenkategorie","Datentyp","Einheit"}
    cands = [c for c in df.columns if c not in meta]
    for sub in prefer_substrings:
        for c in cands:
            if sub.lower() in c.lower():
                return c
    return cands[0] if cands else None

# ---------- 5. EQ (Montel) cekici ----------
def fetch_eq_series(curve_name, d_from, d_to):
    from energyquantified import EnergyQuantified
    from energyquantified.time import Frequency
    eq = EnergyQuantified(api_key=EQ_API_KEY)
    ts = eq.timeseries.load(curve_name, begin=d_from, end=d_to, frequency=Frequency.PT15M)
    out = defaultdict(dict)
    for v in ts:
        if v.value is None:
            continue
        dt = v.date  # EQ CET/CEST local doner
        ds  = dt.strftime("%Y-%m-%d")
        key = f"{dt.hour:02d}-Q{dt.minute//15+1}"
        out[ds][key] = round(float(v.value), 2)
    return dict(sorted(out.items()))

def merge_maps(old, new):
    for ds, kv in new.items():
        old.setdefault(ds, {}).update(kv)
    return dict(sorted(old.items()))

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

# ---------- 6. HTML ----------
def build_html(saldo, aep, id1, id3, spot, vwap):
    dates = sorted(saldo.keys())
    first = dates[0] if dates else ""
    last  = dates[-1] if dates else ""
    template_path = os.path.join(os.path.dirname(__file__), "template.html")
    html = open(template_path).read()
    html = html.replace("__DATA_JSON__", json.dumps(saldo))
    html = html.replace("__AEP_JSON__",  json.dumps(aep))
    html = html.replace("__ID1_JSON__",  json.dumps(id1))
    html = html.replace("__ID3_JSON__",  json.dumps(id3))
    html = html.replace("__SPOT_JSON__", json.dumps(spot))
    html = html.replace("__VWAP_JSON__", json.dumps(vwap))
    html = html.replace("__FIRST_DATE__", first)
    html = html.replace("__LAST_DATE__", last)
    html = html.replace("__UPDATED_AT__", pd.Timestamp.now(tz="Europe/Berlin").strftime("%Y-%m-%d %H:%M"))
    with open("index.html", "w") as f:
        f.write(html)
    print(f"HTML: {first} -> {last}")

# ---------- MAIN ----------
if __name__ == "__main__":
    token = get_token()
    print("Token OK")

    today = date.today()
    first_run = not os.path.exists("data/nrv_saldo.csv")

    if first_run:
        print("Ilk calisma: son 6 ay")
        start = (today - relativedelta(months=6)).replace(day=1)
    else:
        start = today - timedelta(days=2)
    d_from = start.strftime("%Y-%m-%d")
    d_to   = today.strftime("%Y-%m-%d")

    # --- NRV Saldo ---
    if first_run:
        frames = []
        cur = start
        while cur <= today:
            nxt = cur + relativedelta(months=1)
            chunk_to = min(nxt - timedelta(days=1), today)
            try:
                f = fetch_nrv(cur.strftime("%Y-%m-%d"), chunk_to.strftime("%Y-%m-%d"), token)
                frames.append(f)
                print(f"  NRV {cur} -> {chunk_to}: {len(f)}")
            except Exception as e:
                print(f"  NRV {cur} hata: {e}")
            cur = nxt
        nrv_new = pd.concat(frames) if frames else pd.DataFrame()
    else:
        nrv_new = fetch_nrv(d_from, d_to, token)
    nrv_df = update_csv(nrv_new, "data/nrv_saldo.csv")
    saldo = df_to_map(nrv_df, find_value_col(nrv_df, ["Deutschland"]))
    with open("data/nrv_data.json","w") as f: json.dump(saldo, f)
    print(f"Saldo: {len(saldo)} gun")

    # --- AEP Schaetzer ---
    try:
        if first_run:
            frames = []
            cur = start
            while cur <= today:
                nxt = cur + relativedelta(months=1)
                chunk_to = min(nxt - timedelta(days=1), today)
                try:
                    f = fetch_aep(cur.strftime("%Y-%m-%d"), chunk_to.strftime("%Y-%m-%d"), token)
                    frames.append(f)
                    print(f"  AEP {cur} -> {chunk_to}: {len(f)}")
                except Exception as e:
                    print(f"  AEP {cur} hata: {e}")
                cur = nxt
            aep_new = pd.concat(frames) if frames else pd.DataFrame()
        else:
            aep_new = fetch_aep(d_from, d_to, token)
        aep_df = update_csv(aep_new, "data/aep.csv")
        aep_col = find_value_col(aep_df, ["AEP", "Schaetz", "Schätz", "Deutschland"])
        print("AEP kolonlari:", list(aep_df.columns), "| secilen:", aep_col)
        aep = df_to_map(aep_df, aep_col)
    except Exception as e:
        print("AEP HATA (devam ediliyor):", e)
        aep = load_json("data/aep_data.json")
    with open("data/aep_data.json","w") as f: json.dump(aep, f)
    print(f"AEP: {len(aep)} gun")

    # --- EQ: ID1 & VWAP ---
    id1  = load_json("data/id1_data.json")
    vwap = load_json("data/vwap_data.json")
    id3  = load_json("data/id3_data.json")
    spot = load_json("data/spot_data.json")
    if EQ_API_KEY:
        eq_from = start if first_run else (today - timedelta(days=2))
        eq_to   = today + timedelta(days=1)  # EQ end exclusive
        try:
            id1_new = fetch_eq_series("DE Price Intraday VWAP ID1 EUR/MWh EPEX 15min Actual", eq_from, eq_to)
            id1 = merge_maps(id1, id1_new)
            print(f"ID1: +{len(id1_new)} gun (toplam {len(id1)})")
        except Exception as e:
            print("ID1 HATA (devam):", e)
        try:
            vwap_new = fetch_eq_series("DE Price Intraday VWAP EUR/MWh EPEX 15min Actual", eq_from, eq_to)
            vwap = merge_maps(vwap, vwap_new)
            print(f"VWAP: +{len(vwap_new)} gun (toplam {len(vwap)})")
        except Exception as e:
            print("VWAP HATA (devam):", e)
        try:
            id3_new = fetch_eq_series("DE Price Intraday VWAP ID3 EUR/MWh EPEX 15min Actual", eq_from, eq_to)
            id3 = merge_maps(id3, id3_new)
            print(f"ID3: +{len(id3_new)} gun (toplam {len(id3)})")
        except Exception as e:
            print("ID3 HATA (devam):", e)
        try:
            spot_new = fetch_eq_series("DE Price Spot EUR/MWh EPEX 15min Actual", eq_from, eq_to)
            spot = merge_maps(spot, spot_new)
            print(f"SPOT: +{len(spot_new)} gun (toplam {len(spot)})")
        except Exception as e:
            print("SPOT HATA (devam):", e)
    else:
        print("EQ_API_KEY yok, EQ serileri atlandi")
    with open("data/id1_data.json","w") as f: json.dump(id1, f)
    with open("data/vwap_data.json","w") as f: json.dump(vwap, f)
    with open("data/id3_data.json","w") as f: json.dump(id3, f)
    with open("data/spot_data.json","w") as f: json.dump(spot, f)

    build_html(saldo, aep, id1, id3, spot, vwap)
    print("Tamamlandi.")
