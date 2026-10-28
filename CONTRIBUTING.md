# Modo de trabajo

Proyecto personal de investigación. Estas reglas existen para que los resultados sigan siendo creíbles y para que nunca se cuele dinero real ni información privada.

## Ramas

```
main  ◄── PR ──  develop  ◄── PR ──  epic/<épica>  ◄── PR ──  feat|fix|research|docs|test|refactor|chore/<nº-issue>-<descripción>
```

| Rama | Para qué | Reglas |
|---|---|---|
| `main` | Lo que corre en el portátil. Siempre estable. | Solo por PR desde `develop`, con la CI en verde. Protegida. |
| `develop` | Integración de lo terminado. | Solo por PR desde una épica (o un `fix/` urgente), con la CI en verde. Protegida. |
| `epic/<slug>` | Una por épica activa. Sale de `develop`. | Recibe los PR de sus tareas. Al cerrar la épica: PR `epic/<slug>` → `develop`. |
| `<tipo>/<nº>-<slug>` | Una por issue (tarea). Sale de su `epic/`. | Corta: idealmente un PR en menos de una semana. Se borra al fusionar. |

Ejemplo: `research/12-backtest-tendencia-btc`, que sale de `epic/cripto-tendencia` y vuelve a ella.

Antes de abrir un PR, actualiza tu rama con la de destino (`git rebase origin/epic/<slug>`). Para fusionar se usa **squash merge** en las tareas (un commit por issue) y **merge commit** en épica → `develop` → `main`, para conservar la historia.

## Épicas e issues

- **Épica** (etiqueta `épica`): un objetivo grande con su rama `epic/...`. Sus tareas son *sub-issues*.
- **Tarea** (`tarea`): trabajo concreto de ingeniería con criterio de aceptación.
- **Investigación** (`investigación`): una hipótesis que se valida con datos. Se escribe **antes** de mirar resultados: hipótesis, datos, métrica, criterio de éxito y criterio de descarte. Sirve para no ajustar estrategias a beneficios pasados.
- **Bug** (`bug`).
- `bloqueada`: no se trabaja hasta que se cumpla lo que indica (por ejemplo, autorización expresa para dinero real).

## Commits: Conventional Commits en español

```
<tipo>(<ámbito opcional>): <resumen en imperativo, minúsculas, sin punto final>

<cuerpo opcional: el porqué del cambio>

Refs #<issue>
```

Tipos: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `ci`, `build`, `research` (experimentos y backtests).
Ámbitos habituales: `backend`, `frontend`, `estrategias`, `ml`, `ejecucion`, `contabilidad`, `datos`, `infra`.

Ejemplos:
- `feat(estrategias): añade tendencia diaria en BTC con salida por media móvil`
- `fix(ejecucion): rechaza libros cruzados en mercados de corto plazo`
- `research(cripto): backtest walk-forward 2018–2026 frente a comprar y mantener`

Un cambio incompatible se marca con `!` (`feat(api)!: ...`) y lleva `BREAKING CHANGE:` en el cuerpo.

## Definición de terminado (cada PR)

- [ ] La CI pasa: tests del backend, tipos y lint del frontend, y build.
- [ ] Hay tests para la lógica nueva, sobre todo si toca dinero, ejecución o estadística.
- [ ] Si cambia el esquema de la base de datos, hay una migración Alembic y no se borran datos históricos.
- [ ] Si cambia una estrategia, sus parámetros o un escenario: se ha empezado un experimento nuevo (Trading simulado → Reiniciar experimento) para no mezclar resultados.
- [ ] Se han actualizado `PROGRESS.md` y la documentación de `docs/` que corresponda.
- [ ] No contiene secretos, claves, datos de `data/` ni `models/`, ni rutas o datos personales.
- [ ] No añade nada que envíe órdenes reales (`REAL_TRADING_ENABLED` sigue en `False`).

## Reglas de investigación

1. **No inventar ni maquillar resultados.** Si nada funciona, se dice.
2. **No optimizar parámetros mirando el PnL pasado.** Los cambios se justifican antes de ver los resultados y se evalúan en un experimento nuevo.
3. **Una estrategia avanza solo por los filtros** de `docs/PROFITABILITY_GATES.md`. Un buen día no es evidencia.
4. **Dinero real: prohibido** hasta cumplir todo `docs/LIVE_TRADING_DESIGN.md` y con autorización expresa por escrito.

## Qué nunca va al repositorio

`.env`, claves o tokens, la base de datos y sus copias (`data/`), los modelos entrenados (`models/`), los datasets descargados, los entornos virtuales y `node_modules`. El `.gitignore` ya los excluye; aun así, revisa `git status` antes de cada commit.
