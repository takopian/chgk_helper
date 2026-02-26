SELECT 
    t.id
 FROM (
  SELECT q.id as id,
          SUM(50 + COALESCE(aw.weight, 0) + COALESCE(tw.weight, 0)) OVER () AS total_weight,
          SUM(50 + COALESCE(aw.weight, 0) + COALESCE(tw.weight, 0)) OVER (ORDER BY q.id) AS running_weight
  FROM question_pool q
  left join question_authors qa on q.id = qa.question_id
  left join (
    select 
        qa.author_id as author_id,
        sum(CASE WHEN qf.liked THEN 1 ELSE -1 END) as weight
    from question_feedback qf
    left join question_pool q on qf.question_id = q.id
    left join question_authors qa on q.id = qa.question_id
    group by qa.author_id
  ) aw on qa.author_id = aw.author_id
  left join (
    select 
      q.id as question_id, max(coalesce(tw.weight, 0)) as weight
    from question_pool q 
    left join question_tournaments qt on q.id = qt.question_id
    left join (
    select 
            qt.tournament_id as tournament_id,
            sum(CASE WHEN qf.liked THEN 1 ELSE -1 END) as weight
        from question_feedback qf
        left join question_pool q on qf.question_id = q.id
        left join question_tournaments qt on q.id = qt.question_id
        group by qt.tournament_id
    ) tw on qt.tournament_id = tw.tournament_id
    group by q.id 
  ) tw on q.id = tw.question_id
    where coalesce(50 + aw.weight, 0) + coalesce(50 + tw.weight, 0) >= 0 and q.used = false
) t
 WHERE running_weight >= random() * total_weight 
 ORDER BY running_weight
 LIMIT 1;
