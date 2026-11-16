-- Current selections: one row per selected option; withdrawn votes are excluded.
SELECT p.chat_id, p.message_id, v.poll_id, v.voter_kind, v.voter_id,
       v.display_name, v.username, o.text AS selected_option, o.game_id,
       v.received_at AT TIME ZONE 'Europe/Moscow' AS received_msk
FROM poll_votes v
JOIN telegram_polls p ON p.bot_id = v.bot_id AND p.poll_id = v.poll_id
JOIN telegram_poll_options o ON o.bot_id = v.bot_id AND o.poll_id = v.poll_id
                            AND o.option_id = ANY(v.option_ids)
ORDER BY p.chat_id, v.poll_id, v.voter_id, o.option_id;

-- History: [] means withdrawal; previous choices remain in earlier rows.
SELECT p.chat_id, e.poll_id, e.update_id, e.voter_kind, e.voter_id,
       e.display_name, e.username, e.option_ids,
       ARRAY(SELECT o.text FROM telegram_poll_options o
             WHERE o.bot_id = e.bot_id AND o.poll_id = e.poll_id
               AND o.option_id = ANY(e.option_ids) ORDER BY o.option_id) AS choices,
       e.received_at AT TIME ZONE 'Europe/Moscow' AS received_msk
FROM poll_vote_events e
JOIN telegram_polls p ON p.bot_id = e.bot_id AND p.poll_id = e.poll_id
ORDER BY e.received_at, e.update_id;
