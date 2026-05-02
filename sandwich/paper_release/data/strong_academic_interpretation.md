# Strong-tier sandwich attacks: figure interpretation

## Direct answers

1. **Unit-price profitable**: 10,203/13,165 (77.5%). Unit-price profit is the per-token execution-price improvement between the front leg and the back leg (SOL/token).
2. **Total-profit profitable**: 5,060/13,165 (38.4%). Gross gains 354.81 SOL, gross losses -548.46 SOL, net -193.65 SOL.
3. **Tip and fee cost share**: total tx fees 0.144470 SOL, total Jito tips 0.142938 SOL, total explicit cost 0.287408 SOL; median explicit-cost / capital is 0.111 bp.
4. **Multi-victim bundles**: yes, 107 (0.81%) strong sandwiches have more than one victim; the vast majority are single-victim.
5. **Cost / profit / capital relationships**: corr(log(capital), profit) = -0.070; corr(log(capital), log|profit|) = 0.690. Capital looks more like risk exposure than a guarantee of steady return.

## Extended research questions

- **Why do most attacks have unit-price improvement but only a minority realise SOL profit?** See `academic_price_vs_total_profit.png`. There is a gap between price edge and realised P&L driven by inventory size, refill mismatch, and execution error.
- **Do explicit fees and tips explain the losses?** See `academic_fee_tip_costs.png`. No: explicit costs are orders of magnitude smaller than realised losses.
- **Are multi-victim bundles the dominant structure?** See `academic_victims_capital_profit.png`. No: the vast majority of bundles target a single victim.
- **Does capital deliver return or just risk?** Same figure: larger capital widens P&L dispersion, while explicit cost stays roughly flat in size.
- **Do bots exhibit persistent skill? Do token or slot effects explain losses?** See `academic_research_extensions.png`.
