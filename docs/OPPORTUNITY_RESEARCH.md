# Next research lines: ranking

Generated from `backend/app/analytics/research.py` (also shown in Profitability Center → Next markets). Written 2026-10-28.
This is a **qualitative** assessment for a home laptop, small capital and a resident of Spain. It is **not evidence of profit**: every line has to pass the same paper-trading gates.
Regulatory and tax notes are pointers to verify, not legal advice. The text below is in Spanish, the owner's language.

Score = mean of the criteria complexity, liquidity, risk, access, hardware, data, testability, automation (1 = bad, 5 = good; complexity is inverted).

| # | Línea | Puntuación | Capital mínimo | Frecuencia | Desde España |
|---|---|---|---|---|---|
| 1 | Cripto spot: tendencia/momentum diario (BTC, ETH) | 4.25 | ≈100 € | Diaria o semanal | Sí, con exchanges con licencia MiCA (verificar la licencia CASP del exchange elegido) |
| 2 | ETF UCITS: seguimiento de tendencia y rotación mensual | 4.12 | ≈1.000–5.000 € (comisiones mínimas del bróker) | Mensual | Sí, con brókeres regulados con API (p. ej. Interactive Brokers); solo ETF UCITS por PRIIPs |
| 3 | Carry de financiación cripto (spot largo + perpetuo corto) | 3.62 | ≈1.000 € | Diaria (rebalanceo) | Limitado: los derivados cripto para minoristas en la UE están restringidos según el exchange (verificar) |
| 4 | Arbitraje estadístico / pares (acciones o cripto) | 3.38 | ≈5.000 € | Diaria | Sí, con bróker que permita cortos |
| 5 | Mercados de predicción (Polymarket, lo actual) | 3.0 | ≈50 € | Minutos a días | Incierto: no aparece en la lista de bloqueo, pero no consta licencia de juego (DGOJ). Verificar antes de usar dinero real |
| 6 | Arbitraje entre plataformas (mercados de predicción o exchanges) | 2.38 | ≈2.000 € repartidos entre plataformas | Esporádica | Kalshi no está disponible; otras plataformas con licencia española no ofrecen los mismos contratos (verificar) |
| 7 | Market making (proveer liquidez) | 2.25 | ≈500 € | Continua (segundos) | Depende del mercado (Polymarket incierto; cripto vía exchanges MiCA) |
| 8 | Referencia: liquidez remunerada / Letras del Tesoro / fondo monetario | referencia | Bajo | Ninguna | Sí |

## Cripto spot: tendencia/momentum diario (BTC, ETH)

- **Veredicto:** Mejor siguiente línea: datos históricos largos y gratuitos (velas de Binance que ya usamos), baja frecuencia, costes conocidos y API sencilla. La ventaja esperable es modesta y sobre todo de reducción de caídas frente a mantener; hay que demostrarla fuera de muestra.
- **Costes:** 0,1–0,4 % por operación (taker)
- **Regulación y fiscalidad:** Ganancias tributan en IRPF (base del ahorro); Modelo 721 si hay >50.000 € en el extranjero
- **Siguiente paso:** Backtest 2018–2026 de reglas de tendencia con costes, comparado con comprar y mantener; después papel en vivo.

## ETF UCITS: seguimiento de tendencia y rotación mensual

- **Veredicto:** Muy compatible con 'ingresos pasivos': pocas operaciones y evidencia académica larga. Muchas reglas publicadas se han degradado; el objetivo realista es igualar al índice con menos caídas, no batirlo mucho.
- **Costes:** Bajos: comisión por orden + spread
- **Regulación y fiscalidad:** IRPF base del ahorro; brókeres extranjeros: Modelo 720/721 según saldos
- **Siguiente paso:** Conseguir datos diarios de una fuente oficial o con licencia (sin scraping) y comparar contra un ETF global comprado y mantenido.

## Carry de financiación cripto (spot largo + perpetuo corto)

- **Veredicto:** Rentabilidad histórica positiva y medible con datos públicos de funding, pero acceso desde España y riesgo de contraparte (quiebra del exchange) lo hacen poco apto para empezar.
- **Costes:** Comisiones de ambas patas + spread; riesgo de contraparte
- **Regulación y fiscalidad:** Derivados: requisitos de idoneidad; fiscalidad a verificar
- **Siguiente paso:** Solo investigar con datos históricos de funding; no implementar ejecución.

## Arbitraje estadístico / pares (acciones o cripto)

- **Veredicto:** Estrategia muy explotada por fondos; el riesgo de sobreajuste al elegir pares es alto.
- **Costes:** Comisiones + coste de préstamo para cortos
- **Regulación y fiscalidad:** IRPF base del ahorro
- **Siguiente paso:** Posible más adelante si la línea de cripto/ETF demuestra algo.

## Mercados de predicción (Polymarket, lo actual)

- **Veredicto:** Buen laboratorio, mal destino de capital: la única ventaja observada (momentum en cripto 5 min) cabe en pocos euros por operación, compite con bots rápidos y el marco legal en España es dudoso.
- **Costes:** Hasta 7 % de p(1−p) en cripto; 0–5 % según categoría
- **Regulación y fiscalidad:** Probable consideración de juego; tratamiento fiscal a verificar
- **Siguiente paso:** Seguir solo en papel hasta que el Profitability Center dé un veredicto.

## Arbitraje entre plataformas (mercados de predicción o exchanges)

- **Veredicto:** Las diferencias de precio aparentes suelen desaparecer al sumar comisiones, tiempos de transferencia y diferencias en las reglas de resolución. Además nuestro arbitraje interno no ha encontrado nada.
- **Costes:** Comisiones dobles + transferencias + capital inmovilizado
- **Regulación y fiscalidad:** Varias jurisdicciones
- **Siguiente paso:** No priorizar.

## Market making (proveer liquidez)

- **Veredicto:** Con un portátil doméstico se compite contra infraestructura profesional. Simular fills pasivos exige modelar la cola del libro, que no podemos observar: cualquier backtest sería muy optimista.
- **Costes:** Bajos en comisiones; el coste real es la selección adversa
- **Regulación y fiscalidad:** Igual que el mercado subyacente
- **Siguiente paso:** No priorizar.

## Referencia: liquidez remunerada / Letras del Tesoro / fondo monetario

- **Veredicto:** No es una estrategia sino el listón: cualquier bot debe superar esta rentabilidad sin riesgo, después de costes e impuestos, para tener sentido.
- **Costes:** Casi nulos
- **Regulación y fiscalidad:** IRPF base del ahorro
- **Siguiente paso:** Usarla como referencia en todas las comparaciones.

## Recommendation

The next line is **systematic daily crypto spot (BTC/ETH)**. It reuses the Binance data the lab already downloads, has long free history for proper out-of-sample tests, trades at low frequency and is accessible through MiCA-licensed exchanges.
Its benchmark is buy-and-hold, and behind that the risk-free rate. A realistic goal is a smaller drawdown for similar returns, not beating the market by a lot.
