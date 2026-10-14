"""Chatting with Boardy, the onboarding agent, to set up how a client's documents are processed.

Each request is one step of a conversation: a message, an answer to Boardy's
approval question, a retry of a turn that failed, or a sample upload.

Approving is the client's explicit confirmation: the approved configuration
is switched on straight away (activated, with signing secrets for new
webhooks, shown once in that response). Boardy saves it as a draft with its
own limited role; switching on happens here, with the backend's connection.
If that step fails, the conversation shows the configuration as approved but
not switched on, and POST .../activate tries again. The
customer is always the session's user, so a conversation id can only ever
reach that customer's own conversation (the thread id is "<user id>:<id>").

Turns run Boardy's model and tools, so they take seconds. These endpoints are
plain functions, which FastAPI runs in its thread pool, so a slow turn doesn't
hold up other requests.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import BoardyConversation, ClientConfiguration, User
from app.schemas import (
    BoardyApprovalIn,
    BoardyConversationOut,
    BoardyConversationSummaryOut,
    BoardyFeedbackIn,
    BoardyMessageIn,
)
from app.services import boardy

router = APIRouter(prefix="/boardy/conversations", tags=["boardy"])


def _client(user: User) -> User:
    if not user.username:
        # Boardy sets up processing for an address, which starts with the username step.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Choose a username before setting up processing.")
    return user


def _conversation(db: Session, user: User, conversation_id: str) -> BoardyConversation:
    conversation = db.get(BoardyConversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def _version_status(db: Session, user_id: str, version: int | None) -> str | None:
    if version is None:
        return None
    return (
        db.query(ClientConfiguration.status)
        .filter_by(user_id=user_id, version=version)
        .scalar()
    )


def _activation_pending(db: Session, conversation: BoardyConversation, view: dict) -> bool:
    """Approved here, but that version is still a draft: switching it on hasn't happened yet."""
    return bool(view["approved"]) and _version_status(
        db, conversation.user_id, view["saved_version"]) == "draft"


def _out(db: Session, conversation: BoardyConversation, view: dict, **extra) -> BoardyConversationOut:
    active_version = (
        db.query(ClientConfiguration.version)
        .filter_by(user_id=conversation.user_id, status="active")
        .scalar()
    )
    return BoardyConversationOut(
        id=conversation.id,
        created_at=conversation.created_at,
        samples=[{"id": s["id"], "name": s["name"]} for s in conversation.samples],
        feedback=conversation.feedback,
        active_version=active_version,
        activation_pending=_activation_pending(db, conversation, view),
        **view,
        **extra,
    )


def _touch(db: Session, conversation: BoardyConversation) -> None:
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()


@router.get("", response_model=list[BoardyConversationSummaryOut])
def list_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BoardyConversationSummaryOut]:
    rows = (
        db.query(BoardyConversation)
        .filter_by(user_id=current_user.id)
        .order_by(BoardyConversation.updated_at.desc())
        .all()
    )
    return [BoardyConversationSummaryOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("", response_model=BoardyConversationOut, status_code=status.HTTP_201_CREATED)
def start_conversation(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardyConversationOut:
    user = _client(current_user)
    conversation = BoardyConversation(user_id=user.id, samples=[])
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _out(db, conversation, boardy.run(lambda: boardy.conversations().view(user.id, conversation.id)))


@router.get("/{conversation_id}", response_model=BoardyConversationOut)
def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardyConversationOut:
    conversation = _conversation(db, current_user, conversation_id)
    return _out(db, conversation, boardy.run(lambda: boardy.conversations().view(current_user.id, conversation.id)))


@router.post("/{conversation_id}/messages", response_model=BoardyConversationOut)
def send_message(
    conversation_id: str,
    payload: BoardyMessageIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardyConversationOut:
    user = _client(current_user)
    conversation = _conversation(db, user, conversation_id)
    view = boardy.run(lambda: boardy.conversations().send(
        user.id, user.username, conversation.id, payload.text, conversation.samples))
    _touch(db, conversation)
    return _out(db, conversation, view)


@router.post("/{conversation_id}/approval", response_model=BoardyConversationOut)
def answer_approval(
    conversation_id: str,
    payload: BoardyApprovalIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardyConversationOut:
    conversation = _conversation(db, current_user, conversation_id)
    view = boardy.run(lambda: boardy.conversations().answer_approval(
        current_user.id, conversation.id, payload.approved, payload.comments))
    _touch(db, conversation)
    if not _activation_pending(db, conversation, view):
        return _out(db, conversation, view)
    # Approved: switch it on now. If that fails, the approval stands and "Switch on" retries.
    try:
        new_secrets = boardy.switch_on(current_user.id, view["saved_version"])
    except HTTPException as exc:
        return _out(db, conversation, view, activation_error=exc.detail)
    return _out(db, conversation, view, new_secrets=new_secrets)


@router.post("/{conversation_id}/activate", response_model=BoardyConversationOut)
def switch_on(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardyConversationOut:
    """Switches on the configuration approved in this conversation, if that didn't happen yet."""
    conversation = _conversation(db, current_user, conversation_id)
    view = boardy.run(lambda: boardy.conversations().view(current_user.id, conversation.id))
    if not _activation_pending(db, conversation, view):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="There's no approved setup waiting to be switched on.")
    new_secrets = boardy.switch_on(current_user.id, view["saved_version"])
    return _out(db, conversation, view, new_secrets=new_secrets)


@router.post("/{conversation_id}/retry", response_model=BoardyConversationOut)
def retry_turn(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardyConversationOut:
    conversation = _conversation(db, current_user, conversation_id)
    view = boardy.run(lambda: boardy.conversations().retry(current_user.id, conversation.id))
    _touch(db, conversation)
    return _out(db, conversation, view)


@router.post("/{conversation_id}/samples", response_model=BoardyConversationOut)
def add_sample(
    conversation_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardyConversationOut:
    """Stores a sample document for Boardy to read. Boardy sees it from the client's next message."""
    conversation = _conversation(db, current_user, conversation_id)
    limit = boardy.max_sample_bytes()
    data = file.file.read(limit + 1)  # one byte over is enough to know it's too big
    sample = boardy.store_sample(conversation.samples, current_user.id, conversation.id,
                                 file.filename or "sample", file.content_type or "", data)
    conversation.samples = [*conversation.samples, sample]  # a new list, so the change is saved
    _touch(db, conversation)
    return _out(db, conversation, boardy.run(lambda: boardy.conversations().view(current_user.id, conversation.id)))


@router.put("/{conversation_id}/feedback", response_model=BoardyConversationOut)
def save_feedback(
    conversation_id: str,
    payload: BoardyFeedbackIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardyConversationOut:
    """The client's feedback on the conversation, replacing any earlier feedback."""
    conversation = _conversation(db, current_user, conversation_id)
    conversation.feedback = payload.text.strip() or None
    conversation.feedback_at = datetime.now(timezone.utc) if conversation.feedback else None
    _touch(db, conversation)
    return _out(db, conversation, boardy.run(lambda: boardy.conversations().view(current_user.id, conversation.id)))
