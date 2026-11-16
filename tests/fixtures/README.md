GotQuestions regression fixtures captured on 2026-11-12:

- `home.html`: https://gotquestions.online/
- `pack.html`: https://gotquestions.online/pack/7365 (36 questions).

Only inline Next.js transport scripts and package links are retained. Scripts are
parsed as data and never executed. These fixtures verify the actual site's schema;
synthetic tests cover escaping, chunk boundaries, references and failure cases.

Run offline from the repository root:

    python -m unittest discover -s tests -v
