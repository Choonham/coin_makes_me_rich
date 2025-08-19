# ===================================================================================
#   api/routes_orders.py: 주문 관련 API 라우터
# ===================================================================================
from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException

from app.state.store import state_store

router = APIRouter()

@router.get("/history",
            response_model=List[Any],
            summary="Get Order History",
            description="Retrieves the most recent order history from the state store.")
async def get_order_history(limit: int = 100):
    """
    Fetches the most recent order history from the in-memory state store.
    This list is periodically updated from the Bybit API.
    """
    try:
        # state_store에서 직접 주문 내역을 가져옵니다.
        order_history = state_store.get_system_state().order_history
        return order_history[:limit]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# TODO: Add routes for placing new orders, canceling orders, etc.