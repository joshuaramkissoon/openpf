from fastapi import APIRouter, HTTPException

from app.schemas.strategy import BacktestRequest, BacktestResponse
from app.services.strategy_service import run_ma_crossover_backtest

router = APIRouter(prefix="/strategy", tags=["strategy"])


@router.post("/backtest", response_model=BacktestResponse)
def backtest(payload: BacktestRequest) -> BacktestResponse:
    try:
        result = run_ma_crossover_backtest(
            payload.symbol,
            lookback_days=payload.lookback_days,
            fast_window=payload.fast_window,
            slow_window=payload.slow_window,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"backtest failed: {exc}") from exc

    return BacktestResponse(
        symbol=result.symbol,
        lookback_days=result.lookback_days,
        fast_window=result.fast_window,
        slow_window=result.slow_window,
        trades=result.trades,
        cagr=result.cagr,
        max_drawdown=result.max_drawdown,
        sharpe=result.sharpe,
        win_rate=result.win_rate,
        equity_curve=result.equity_curve,
    )
