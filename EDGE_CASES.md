# Edge cases

- A repo with zero open PRs and zero board cards must still render its row (empty state, not a crash).
- Board cards whose issue was transferred to another repo keep their old URL; follow the redirect instead of dropping the card.
