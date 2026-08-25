import os
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
import requests
from functools import wraps
from dotenv import load_dotenv
from collections import defaultdict
import datetime

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "caja_clara_super_secret")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DB_TX = os.environ.get("NOTION_DB_TX")
NOTION_DB_CLIENTES = os.environ.get("NOTION_DB_CLIENTES")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "908590")

headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == APP_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Contraseña incorrecta")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/clientes', methods=['GET'])
@login_required
def get_clientes():
    try:
        res = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DB_CLIENTES}/query", headers=headers, json={})
        results = res.json().get('results', [])
        clientes_data = {}
        for r in results:
            props = r['properties']
            if 'Nombre' in props and props['Nombre']['title']:
                nombre = props['Nombre']['title'][0]['text']['content']
                proyectos = []
                if 'Proyectos' in props and props['Proyectos'].get('rich_text'):
                    texto_proys = props['Proyectos']['rich_text'][0]['text']['content']
                    proyectos = [p.strip() for p in texto_proys.split(',') if p.strip()]
                clientes_data[nombre] = proyectos
        return jsonify(clientes_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/clientes', methods=['POST'])
@login_required
def add_cliente():
    nombre = request.json.get('nombre')
    if nombre:
        data = {
            "parent": {"database_id": NOTION_DB_CLIENTES},
            "properties": {
                "Nombre": {"title": [{"text": {"content": nombre}}]}
            }
        }
        requests.post("https://api.notion.com/v1/pages", headers=headers, json=data)
    return jsonify({"status": "success"})

@app.route('/api/transacciones', methods=['POST'])
@login_required
def crear_transaccion():
    data = request.json
    try:
        notion_data = {
            "parent": { "database_id": NOTION_DB_TX },
            "properties": {
                "Concepto": { "title": [ { "text": { "content": data['concepto'] } } ] },
                "Monto": { "number": float(data['monto']) },
                "Tipo": { "select": { "name": data['tipo'] } },
                "Categoría": { "select": { "name": data['categoria'] } },
                "Estado": { "select": { "name": data['estado'] } },
                "Fecha": { "date": { "start": data['fecha'] } },
                "Cliente o Entidad": { "rich_text": [ { "text": { "content": data['cliente'] } } ] }
            }
        }
        
        if data.get('proyecto'):
            notion_data["properties"]["Proyecto"] = { "rich_text": [ { "text": { "content": data['proyecto'] } } ] }
            
        if data.get('fecha_vencimiento'):
            notion_data["properties"]["Fecha Vencimiento"] = { "date": { "start": data['fecha_vencimiento'] } }

        res = requests.post("https://api.notion.com/v1/pages", headers=headers, json=notion_data)
        if res.status_code == 200:
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": res.text}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/proyectos', methods=['POST'])
@login_required
def get_proyectos():
    try:
        cliente = request.json.get('cliente')
        query = {
            "filter": {
                "property": "Cliente o Entidad",
                "rich_text": { "equals": cliente }
            }
        }
        res = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DB_TX}/query", headers=headers, json=query)
        results = res.json().get('results', [])
        
        proyectos = set()
        for r in results:
            props = r['properties']
            proj = props.get('Proyecto', {}).get('rich_text', [])
            if proj:
                proyectos.add(proj[0]['text']['content'].strip())
        
        return jsonify(list(proyectos))
    except Exception as e:
        return jsonify([])

@app.route('/api/reporte', methods=['POST'])
@login_required
def generar_reporte():
    data = request.json
    cliente = data['cliente']
    mes = data['mes']
    proyecto_filtro = data.get('proyecto', '').strip()
    
    filters = [
        { "property": "Cliente o Entidad", "rich_text": { "equals": cliente } },
        { "property": "Fecha", "date": { "on_or_after": f"{mes}-01" } },
        { "property": "Fecha", "date": { "before": f"{get_next_month(mes)}-01" } }
    ]
    
    if proyecto_filtro:
        filters.append({ "property": "Proyecto", "rich_text": { "equals": proyecto_filtro } })
    
    query = { "filter": { "and": filters } }
    
    res = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DB_TX}/query", headers=headers, json=query)
    results = res.json().get('results', [])
    
    ingresos_totales = 0
    gastos_totales = 0
    gastos_cat = defaultdict(float)
    gastos_proj = defaultdict(float)
    por_cobrar = []
    por_pagar = []
    
    for r in results:
        props = r['properties']
        try:
            monto = props['Monto']['number'] or 0
            tipo = props['Tipo']['select']['name']
            estado = props['Estado']['select']['name']
            concepto = props['Concepto']['title'][0]['text']['content']
            cat = props['Categoría']['select']['name'] if props.get('Categoría') and props['Categoría'].get('select') else 'Otros'
            proj = props.get('Proyecto', {}).get('rich_text', [])
            proj_name = proj[0]['text']['content'] if proj else 'General'
            
            # Helper para fecha de vencimiento
            f_venc = ""
            if props.get('Fecha Vencimiento') and props['Fecha Vencimiento'].get('date'):
                f_venc = props['Fecha Vencimiento']['date']['start']
            
            if estado == 'Pagado':
                if tipo == 'Ingreso':
                    ingresos_totales += monto
                elif tipo == 'Gasto':
                    gastos_totales += monto
                    gastos_cat[cat] += monto
                    gastos_proj[proj_name] += monto
            elif estado == 'Pendiente de Cobro':
                por_cobrar.append({"concepto": concepto, "monto": monto, "vencimiento": f_venc})
            elif estado == 'Pendiente de Pago':
                por_pagar.append({"concepto": concepto, "monto": monto, "vencimiento": f_venc})
        except Exception as e:
            continue
            
    return jsonify({
        "status": "success",
        "data": {
            "ingresos": ingresos_totales,
            "gastos": gastos_totales,
            "neto": ingresos_totales - gastos_totales,
            "gastos_por_categoria": dict(gastos_cat),
            "por_cobrar": por_cobrar,
            "por_pagar": por_pagar
        }
    })

def get_next_month(yyyy_mm):
    y, m = map(int, yyyy_mm.split('-'))
    if m == 12:
        return f"{y+1}-01"
    return f"{y}-{str(m+1).zfill(2)}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
