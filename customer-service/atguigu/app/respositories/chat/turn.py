from datetime import datetime

from sqlalchemy import select, exists, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from atguigu.models.models import ConversationTurn, Conversation


class ConversationTurnRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def is_activate_turn(self, conversation_id: str) -> bool:
        return bool(await self.session.scalar(
            select(
                exists()
                .where(ConversationTurn.conversation_id == conversation_id,
                       ConversationTurn.status.in_(["COLLECTING", "RUNNING"]))
            )
        ))

    async def find_conversation_by_id(self, conv_id: str) -> ConversationTurn | None:
        return await self.session.scalar(
            select(ConversationTurn)
            .where(ConversationTurn.conversation_id == conv_id,
                   ConversationTurn.status == "COLLECTING")
            .with_for_update()  #悲观行锁，一个是事务执行了带for_update的查询后，直到该事务显式调用commit()或者rollback()
            #目标行都会被锁住
        )

    def add_turn(self, turn: ConversationTurn):
        self.session.add(turn)

    async def find_turn_conversation_by_id(self, turn_id: str) -> tuple[ConversationTurn, Conversation]:
        result = await self.session.execute(
            select(ConversationTurn, Conversation)
            .join(
                Conversation,
                Conversation.id == ConversationTurn.conversation_id
            )
            .where(ConversationTurn.id == turn_id)
            .with_for_update(of=Conversation)
            #如果不写of=Conversation ,生成的sql式select  from conversation_turns join conversation  for update
            #数据库默认会把两张表匹配的行为全部加上排他锁，加上of=conversation只锁Conversation表
        )
        #解包的conversationTurn，conversation元组
        return result.tuples().one()

    async def list_expired_running_turns(
            self,
            now: datetime
    ) -> list[ConversationTurn]:
        """查询租约已经过期的全部运行中 Turn。"""
        result = await self.session.scalars(
            select(ConversationTurn)
            .where(
                ConversationTurn.status == "RUNNING",
                ConversationTurn.locked_until <= now
            )
        )
        return list(result.all())

    async def find_ready_turn_conversation_by_lock(self, now: datetime) -> tuple[ConversationTurn, Conversation] | None:
        """
        能够被领走的条件是
        1. turn的状态是 COLLECTING
        2. turn时间是否比当前时间要小
        3. 回话的模式是AI
        :param now:
        :return:
        """
        running_turn = aliased(ConversationTurn)
        results = await self.session.execute(
            select(ConversationTurn, Conversation)
            .join(
                Conversation,
                ConversationTurn.conversation_id == Conversation.id
            )
            .where(
                ConversationTurn.status == "COLLECTING",
                ConversationTurn.collect_until <= now,
                Conversation.mode == "AI",
                ~exists(
                    select(1).where(
                        and_(
                            running_turn.conversation_id == ConversationTurn.conversation_id,
                            running_turn.status == "RUNNING"
                        )
                    )
                )
            )
            .order_by(ConversationTurn.collect_until, ConversationTurn.created_at)
            .limit(1)
            .with_for_update()
        )

        return results.tuples().one_or_none()
