-- Speed up chat message loads filtered by conversation_id + user_id.
CREATE INDEX IF NOT EXISTS idx_chat_messages_conv_user_created
ON chat_messages (conversation_id, user_id, created_at DESC);
