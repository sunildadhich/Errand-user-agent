from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import SessionLocal, init_db
from app.schemas import ChatRequest, ChatResponse
from app.agent.orchestrator import ErrandAgent
from app.models import BookingAttempt, Trace, UserEvent
from app.tools.mock_services import MockErrandTools, ToolError

app = FastAPI(title="Errand Agent", version="0.1.0")


@app.on_event("startup")
def startup():
    init_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    agent = ErrandAgent(db)
    conversation_id, reply, state, data = agent.handle(
        request.user_id, request.message, request.conversation_id
    )
    return ChatResponse(conversation_id=conversation_id, reply=reply, state=state, data=data)


@app.get("/trace/{conversation_id}")
def conversation_trace(conversation_id: str, db: Session = Depends(get_db)):
    rows = db.query(Trace).order_by(Trace.id).all()
    runs = {row.run_id for row in rows if row.payload.get("conversation_id") == conversation_id}
    return {"events": [{"run_id": row.run_id, "event": row.event, "payload": row.payload,
                        "created_at": row.created_at.isoformat()} for row in rows if row.run_id in runs]}


@app.post("/mock/provider-cancel/{booking_id}")
def simulate_provider_cancel(booking_id: str, db: Session = Depends(get_db)):
    attempt = db.query(BookingAttempt).filter_by(booking_id=booking_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Booking not found")
    try:
        MockErrandTools(db=db).simulate_provider_cancellation(booking_id)
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=exc.code)
    db.add(UserEvent(user_id=attempt.user_id, kind="provider_cancelled", payload={"booking_id": booking_id}))
    db.commit()
    return {"status": "provider_cancelled", "booking_id": booking_id, "user_id": attempt.user_id}


@app.get("/notifications/{user_id}")
def notifications(user_id: str, db: Session = Depends(get_db)):
    reply = ErrandAgent(db).check_notifications(user_id)
    return {"notification": reply}
