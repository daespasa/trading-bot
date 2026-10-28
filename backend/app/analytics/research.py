"""Ranking of future research lines (docs/OPPORTUNITY_RESEARCH.md explains each one).
Qualitative, written 2026-10-28 and NOT evidence of profitability: each line still has to pass the same
paper-trading gates. Regulatory notes are pointers to verify, not legal advice. Text is in Spanish (owner's language).
Criteria are 1 (bad) .. 5 (good) for the owner's situation: home laptop, small capital, resident in Spain.
`complexity` is inverted in the score (5 = hardest). The score is the plain mean of the criteria, so the ranking
is reproducible and its inputs are visible."""

OPPORTUNITIES = [
    {
        "id": "crypto_trend", "access": 4, "name": "Cripto spot: tendencia/momentum diario (BTC, ETH)",
        "capital_min": "≈100 €", "complexity": 2, "costs": "0,1–0,4 % por operación (taker)", "liquidity": 5, "risk": 2,
        "spain_access": "Sí, con exchanges con licencia MiCA (verificar la licencia CASP del exchange elegido)",
        "regulatory": "Ganancias tributan en IRPF (base del ahorro); Modelo 721 si hay >50.000 € en el extranjero",
        "frequency": "Diaria o semanal", "hardware": 5, "data": 5, "testability": 4, "automation": 5,
        "verdict": "Mejor siguiente línea: datos históricos largos y gratuitos (velas de Binance que ya usamos), "
                   "baja frecuencia, costes conocidos y API sencilla. La ventaja esperable es modesta y sobre todo de "
                   "reducción de caídas frente a mantener; hay que demostrarla fuera de muestra.",
        "next_step": "Backtest 2018–2026 de reglas de tendencia con costes, comparado con comprar y mantener; después papel en vivo.",
    },
    {
        "id": "etf_rotation", "access": 5, "name": "ETF UCITS: seguimiento de tendencia y rotación mensual",
        "capital_min": "≈1.000–5.000 € (comisiones mínimas del bróker)", "complexity": 2, "costs": "Bajos: comisión por orden + spread",
        "liquidity": 5, "risk": 3, "spain_access": "Sí, con brókeres regulados con API (p. ej. Interactive Brokers); solo ETF UCITS por PRIIPs",
        "regulatory": "IRPF base del ahorro; brókeres extranjeros: Modelo 720/721 según saldos",
        "frequency": "Mensual", "hardware": 5, "data": 3, "testability": 4, "automation": 4,
        "verdict": "Muy compatible con 'ingresos pasivos': pocas operaciones y evidencia académica larga. Muchas reglas "
                   "publicadas se han degradado; el objetivo realista es igualar al índice con menos caídas, no batirlo mucho.",
        "next_step": "Conseguir datos diarios de una fuente oficial o con licencia (sin scraping) y comparar contra un ETF global comprado y mantenido.",
    },
    {
        "id": "prediction_markets", "access": 1, "name": "Mercados de predicción (Polymarket, lo actual)",
        "capital_min": "≈50 €", "complexity": 3, "costs": "Hasta 7 % de p(1−p) en cripto; 0–5 % según categoría",
        "liquidity": 2, "risk": 3, "spain_access": "Incierto: no aparece en la lista de bloqueo, pero no consta licencia de juego (DGOJ). Verificar antes de usar dinero real",
        "regulatory": "Probable consideración de juego; tratamiento fiscal a verificar",
        "frequency": "Minutos a días", "hardware": 4, "data": 4, "testability": 3, "automation": 4,
        "verdict": "Buen laboratorio, mal destino de capital: la única ventaja observada (momentum en cripto 5 min) "
                   "cabe en pocos euros por operación, compite con bots rápidos y el marco legal en España es dudoso.",
        "next_step": "Seguir solo en papel hasta que el Profitability Center dé un veredicto.",
    },
    {
        "id": "funding_carry", "access": 2, "name": "Carry de financiación cripto (spot largo + perpetuo corto)",
        "capital_min": "≈1.000 €", "complexity": 3, "costs": "Comisiones de ambas patas + spread; riesgo de contraparte",
        "liquidity": 4, "risk": 3, "spain_access": "Limitado: los derivados cripto para minoristas en la UE están restringidos según el exchange (verificar)",
        "regulatory": "Derivados: requisitos de idoneidad; fiscalidad a verificar",
        "frequency": "Diaria (rebalanceo)", "hardware": 5, "data": 4, "testability": 4, "automation": 4,
        "verdict": "Rentabilidad histórica positiva y medible con datos públicos de funding, pero acceso desde España y "
                   "riesgo de contraparte (quiebra del exchange) lo hacen poco apto para empezar.",
        "next_step": "Solo investigar con datos históricos de funding; no implementar ejecución.",
    },
    {
        "id": "market_making", "access": 2, "name": "Market making (proveer liquidez)",
        "capital_min": "≈500 €", "complexity": 5, "costs": "Bajos en comisiones; el coste real es la selección adversa",
        "liquidity": 3, "risk": 4, "spain_access": "Depende del mercado (Polymarket incierto; cripto vía exchanges MiCA)",
        "regulatory": "Igual que el mercado subyacente",
        "frequency": "Continua (segundos)", "hardware": 2, "data": 2, "testability": 1, "automation": 3,
        "verdict": "Con un portátil doméstico se compite contra infraestructura profesional. Simular fills pasivos "
                   "exige modelar la cola del libro, que no podemos observar: cualquier backtest sería muy optimista.",
        "next_step": "No priorizar.",
    },
    {
        "id": "cross_platform_arb", "access": 1, "name": "Arbitraje entre plataformas (mercados de predicción o exchanges)",
        "capital_min": "≈2.000 € repartidos entre plataformas", "complexity": 4, "costs": "Comisiones dobles + transferencias + capital inmovilizado",
        "liquidity": 2, "risk": 3, "spain_access": "Kalshi no está disponible; otras plataformas con licencia española no ofrecen los mismos contratos (verificar)",
        "regulatory": "Varias jurisdicciones", "frequency": "Esporádica", "hardware": 4, "data": 2, "testability": 2, "automation": 3,
        "verdict": "Las diferencias de precio aparentes suelen desaparecer al sumar comisiones, tiempos de transferencia "
                   "y diferencias en las reglas de resolución. Además nuestro arbitraje interno no ha encontrado nada.",
        "next_step": "No priorizar.",
    },
    {
        "id": "stat_arb_pairs", "access": 4, "name": "Arbitraje estadístico / pares (acciones o cripto)",
        "capital_min": "≈5.000 €", "complexity": 4, "costs": "Comisiones + coste de préstamo para cortos",
        "liquidity": 4, "risk": 3, "spain_access": "Sí, con bróker que permita cortos", "regulatory": "IRPF base del ahorro",
        "frequency": "Diaria", "hardware": 4, "data": 3, "testability": 3, "automation": 4,
        "verdict": "Estrategia muy explotada por fondos; el riesgo de sobreajuste al elegir pares es alto.",
        "next_step": "Posible más adelante si la línea de cripto/ETF demuestra algo.",
    },
    {
        "id": "cash_baseline", "access": 5, "name": "Referencia: liquidez remunerada / Letras del Tesoro / fondo monetario",
        "capital_min": "Bajo", "complexity": 1, "costs": "Casi nulos", "liquidity": 5, "risk": 5,
        "spain_access": "Sí", "regulatory": "IRPF base del ahorro", "frequency": "Ninguna", "hardware": 5, "data": 5,
        "testability": 5, "automation": 5, "score": None,
        "verdict": "No es una estrategia sino el listón: cualquier bot debe superar esta rentabilidad sin riesgo, "
                   "después de costes e impuestos, para tener sentido.",
        "next_step": "Usarla como referencia en todas las comparaciones.",
    },
]


CRITERIA = ("complexity", "liquidity", "risk", "access", "hardware", "data", "testability", "automation")


def ranked() -> list[dict]:
    out = []
    for o in OPPORTUNITIES:
        vals = [6 - o["complexity"] if c == "complexity" else o[c] for c in CRITERIA]
        out.append(o | {"score": None if o["id"] == "cash_baseline" else round(sum(vals) / len(vals), 2)})
    return sorted(out, key=lambda o: -(o["score"] or 0))
