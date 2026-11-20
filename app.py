import json, sqlite3
from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st

BASE = Path(__file__).parent
DB = BASE / "data" / "monitor.db"
DB.parent.mkdir(exist_ok=True)

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS profiles(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, mode TEXT,
          criteria TEXT, created TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS listings(
          id INTEGER PRIMARY KEY AUTOINCREMENT, external_id TEXT UNIQUE,
          url TEXT, make TEXT, model TEXT, year INTEGER, price REAL,
          mileage INTEGER, fuel TEXT, engine_l REAL, gearbox TEXT,
          location TEXT, title TEXT, description TEXT, first_seen TEXT);
        CREATE TABLE IF NOT EXISTS matches(
          profile_id INTEGER, listing_id INTEGER, matched_at TEXT,
          PRIMARY KEY(profile_id, listing_id));
        """)

def profiles():
    with db() as c:
        return c.execute("SELECT * FROM profiles ORDER BY id DESC").fetchall()

def norm(x): return str(x or "").strip().casefold()

def num(x):
    try:
        if x is None or pd.isna(x) or x == "": return None
        return float(x)
    except (ValueError, TypeError): return None

def match(row, cr, mode):
    tests = []
    for f in ("make", "model", "fuel", "gearbox"):
        if cr.get(f):
            tests.append(norm(cr[f]) in norm(row[f]))
    for f, lo, hi in (
        ("year","year_min","year_max"), ("price","price_min","price_max"),
        ("mileage","mileage_min","mileage_max"), ("engine_l","engine_min","engine_max")
    ):
        v = num(row[f])
        if cr.get(lo) is not None: tests.append(v is not None and v >= float(cr[lo]))
        if cr.get(hi) is not None: tests.append(v is not None and v <= float(cr[hi]))
    kws = [x.strip() for x in cr.get("keywords","").split(",") if x.strip()]
    if kws:
        hay = " ".join(norm(row[f]) for f in ("title","description","make","model","fuel","gearbox"))
        kwtests = [norm(k) in hay for k in kws]
        tests.append(any(kwtests) if mode == "abstract" else all(kwtests))
    if not tests: return True
    if mode == "flexible": return sum(bool(x) for x in tests) >= max(1, len(tests)-1)
    return all(tests)

def import_csv(df):
    if "external_id" not in df.columns: raise ValueError("Нужен столбец external_id")
    fields = ["external_id","url","make","model","year","price","mileage","fuel","engine_l","gearbox","location","title","description"]
    now = datetime.now().isoformat(timespec="seconds")
    added = 0
    with db() as c:
        for _, r in df.iterrows():
            vals = [None if f not in df.columns or pd.isna(r.get(f)) else r.get(f) for f in fields]
            try:
                c.execute(f"INSERT INTO listings({','.join(fields)},first_seen) VALUES({','.join(['?']*len(fields))},?)", vals+[now])
                added += 1
            except sqlite3.IntegrityError: pass
        ps = c.execute("SELECT * FROM profiles WHERE active=1").fetchall()
        ls = c.execute("SELECT * FROM listings ORDER BY id DESC LIMIT ?", (max(added,1),)).fetchall()
        for p in ps:
            cr = json.loads(p["criteria"])
            for l in ls:
                if match(dict(l), cr, p["mode"]):
                    c.execute("INSERT OR IGNORE INTO matches VALUES(?,?,?)", (p["id"],l["id"],now))
    return added

init()
st.set_page_config(page_title="Autoplius Monitor", page_icon="🚗", layout="centered")
st.markdown("""
<style>
.block-container {max-width: 760px; padding-top: 1rem; padding-bottom: 4rem;}
div[data-testid="stForm"] {border-radius:14px;}
.stButton>button, .stDownloadButton>button {width:100%; min-height:44px; border-radius:10px;}
div[data-testid="stMetric"] {background:rgba(120,120,120,.08); padding:12px; border-radius:12px;}
</style>
""", unsafe_allow_html=True)

st.title("🚗 Autoplius Monitor")
st.caption("Мобильная версия · управление поиском автомобилей")

page = st.navigation([
    st.Page("app.py", title="Главная", icon="🏠", default=True),
    st.Page("app.py", title="Профили", icon="🔎"),
    st.Page("app.py", title="Объявления", icon="🚘"),
    st.Page("app.py", title="Импорт", icon="📥"),
]) if False else st.radio("Раздел", ["Главная", "Поисковые профили", "Объявления", "Импорт"], horizontal=True)

if page in ("Главная","Поисковые профили"):
    st.subheader("Новый поисковый профиль")
    with st.form("profile"):
        name = st.text_input("Название профиля", "Audi A6 Allroad")
        mode_label = st.selectbox("Логика поиска", ["Строгая", "Гибкая", "Абстрактная"])
        mode = {"Строгая":"strict","Гибкая":"flexible","Абстрактная":"abstract"}[mode_label]
        make = st.text_input("Марка", "Audi")
        model = st.text_input("Модель / семейство", "A6 Allroad")
        a,b = st.columns(2)
        with a:
            y1 = st.number_input("Год от · 0 = любой", 0, 2100, 2018)
            pmax = st.number_input("Цена до · 0 = любая (€)", 0, 1000000, 30000, step=1000)
            mmax = st.number_input("Пробег до · 0 = любой", 0, 1000000, 200000, step=10000)
        with b:
            y2 = st.number_input("Год до · 0 = любой", 0, 2100, 2023)
            fuel = st.selectbox("Топливо", ["Любое","Дизель","Бензин","Гибрид","Электро","LPG"])
            gearbox = st.selectbox("Коробка", ["Любая","Автомат","Механика"])
        keywords = st.text_input("Дополнительные слова через запятую", "quattro, leather")
        submitted = st.form_submit_button("Сохранить профиль", type="primary")
        if submitted:
            if y1 and y2 and y1 > y2:
                st.error("Год начала не может быть больше конечного.")
            else:
                cr = {
                    "make":make or None, "model":model or None,
                    "fuel":None if fuel=="Любое" else fuel,
                    "gearbox":None if gearbox=="Любая" else gearbox,
                    "year_min":y1 or None, "year_max":y2 or None,
                    "price_min":None, "price_max":pmax or None,
                    "mileage_min":None, "mileage_max":mmax or None,
                    "engine_min":None, "engine_max":None, "keywords":keywords
                }
                with db() as c:
                    c.execute("INSERT INTO profiles(name,mode,criteria,created) VALUES(?,?,?,?)",
                              (name,mode,json.dumps(cr,ensure_ascii=False),datetime.now().isoformat(timespec="seconds")))
                st.success("Профиль сохранён.")
    st.divider()
    st.subheader("Твои профили")
    ps = profiles()
    if not ps: st.info("Создай первый профиль — он появится здесь.")
    for p in ps:
        with st.expander(f"{p['name']} · {p['mode']}"):
            st.json(json.loads(p["criteria"]))

elif page == "Объявления":
    st.subheader("Объявления")
    with db() as c:
        df = pd.read_sql_query("SELECT * FROM listings ORDER BY first_seen DESC", c)
        mf = pd.read_sql_query("""SELECT p.name AS Профиль,l.make AS Марка,l.model AS Модель,
        l.year AS Год,l.price AS Цена,l.mileage AS Пробег,l.url AS Ссылка,m.matched_at AS Найдено
        FROM matches m JOIN profiles p ON p.id=m.profile_id JOIN listings l ON l.id=m.listing_id
        ORDER BY m.matched_at DESC""", c)
    st.metric("Всего объявлений", len(df))
    st.metric("Совпадений с профилями", len(mf))
    if not mf.empty: st.dataframe(mf, use_container_width=True, hide_index=True)
    elif df.empty: st.info("Пока нет объявлений. Добавь CSV в разделе «Импорт».")
    if not df.empty:
        with st.expander("Вся база объявлений"):
            st.dataframe(df, use_container_width=True, hide_index=True)

else:
    st.subheader("Импорт данных")
    st.info("Это тестовый способ наполнить базу до подключения разрешённого источника Autoplius.")
    sample = "external_id,url,make,model,year,price,mileage,fuel,engine_l,gearbox,location,title,description\ndemo-001,https://example.com/1,Audi,A6 Allroad,2020,27900,142000,Дизель,3.0,Автомат,Vilnius,Audi A6 Allroad 3.0 TDI,quattro leather\n"
    st.download_button("Скачать пример CSV", sample, "sample_listings.csv", "text/csv")
    upload = st.file_uploader("Выбери CSV", type=["csv"])
    if upload:
        try:
            df = pd.read_csv(upload)
            st.dataframe(df.head(), use_container_width=True)
            if st.button("Импортировать", type="primary"):
                st.success(f"Добавлено новых объявлений: {import_csv(df)}")
        except Exception as e: st.error(str(e))

st.caption("Источник Autoplius пока не подключён. Используй только разрешённые способы получения данных.")
