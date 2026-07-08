# Q2 2026 Business Review

*Northwind Analytics — Quarterly Performance and Outlook · Ava Chen, Marcus Webb · 2026-07-01*

## Executive Summary

Revenue grew **18% quarter-over-quarter** to $4.2M, driven primarily by the enterprise tier. Independent analysts project the segment to double by 2027.[^1]

> [!TIP]
> Net revenue retention reached 124%, the highest in company history.

### What Worked

- Enterprise self-serve onboarding cut sales cycle from 42 to 11 days
- Usage-based pricing adopted by 61% of new accounts
- Churn fell to 1.1% monthly, below the B2B SaaS median of 1.6%[^2]
- EU region launch
  - Frankfurt data residency unlocked 14 stalled deals

### Risks

1. Concentration: top 3 customers are 31% of ARR
1. Infra spend growing faster than revenue (see cost sheet)

> The fastest-growing companies in this cycle are the ones that made usage pricing boring and predictable.
>
> — Meridian Research, State of SaaS 2026

---

### Integration Example

Partners embed reporting via the `v2 API`, documented at [developers.northwind.dev](https://developers.northwind.dev).

```python
from northwind import Client

report = Client(api_key).reports.create(
    quarter="2026-Q2",
    format="pdf",
)
```

### Key Metrics

| Metric | Q1 2026 | Q2 2026 | Change |
| --- | --- | --- | --- |
| Revenue | $3.6M | $4.2M | **\+18%** |
| Gross margin | 71% | 74% | \+3 pts |
| Monthly churn | 1\.4% | 1\.1% | \-0.3 pts |
| Headcount | 58 | 64 | \+6 |

*Quarter-over-quarter comparison*

## Sheet: Revenue

| Month | Revenue | Costs | Margin |
| --- | --- | --- | --- |
| April | 1310000 | 361000 | `=(B2-C2)/B2` |
| May | 1385000 | 366000 | `=(B3-C3)/B3` |
| June | 1505000 | 377000 | `=(B4-C4)/B4` |
| Total | `=SUM(B2:B4)` | `=SUM(C2:C4)` | `=(B5-C5)/B5` |

## Sheet: Headcount

| Team | Q1 | Q2 |
| --- | --- | --- |
| Engineering | 26 | 29 |
| Sales | 14 | 16 |
| Support | 9 | 10 |
| G&A | 9 | 9 |

[^1]: State of SaaS 2026 — Meridian Research (2026-05-14), https://example.com/meridian/state-of-saas-2026
[^2]: B2B SaaS Retention Benchmarks — OpenComps (2026-03-02), https://example.com/opencomps/retention-2026
