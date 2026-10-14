# Calculation Methodology

This document describes the formulas currently implemented in Arb Foundry. All examples are deterministic illustrations, not current market signals or promises of execution.

## 1. Numeric conventions

- Prices, quantities, fees, and PnL use Python Decimal to avoid binary floating-point artifacts in financial arithmetic.
- A market symbol is normalized as BASE/QUOTE; quantity is measured in base-asset units and notionals/PnL in the quote asset.
- Fee rates are decimal fractions: 0.001 = 0.1%, not 0.001%.
- Spread in basis points (bps) uses 1 bp = 0.01%; therefore 1% = 100 bps.
- Unless a future accounting layer says otherwise, fees are charged on each leg's quote notional.

## 2. Order-book VWAP for a target quantity

A marketable buy consumes ask levels from the lowest ask upward. A marketable sell consumes bid levels from the highest bid downward.

For target base quantity \(Q\), price \(P_i\), and fill quantity \(q_i\) at level \(i\):

\[
\mathrm{VWAP}(Q) = \frac{\sum_i P_i q_i}{Q}, \quad \sum_i q_i = Q
\]

The scanner and simulator only call a full-size VWAP valid when visible levels cover the full requested quantity. The paper simulator can optionally model a paired partial fill: the simulated quantity is the minimum liquidity available on the buy and sell legs. It does not assume the unfilled remainder is executed.

**Illustration:** buy 2 BTC at 100 for 1 BTC and 102 for the next 1 BTC:

\[
\mathrm{Buy\ VWAP} = \frac{100\times1 + 102\times1}{2}=101
\]

Sell 2 BTC at 105 for 1 BTC and 103 for the next 1 BTC:

\[
\mathrm{Sell\ VWAP} = \frac{105\times1 + 103\times1}{2}=104
\]

## 3. Gross PnL and spread

Let \(Q\) be the paired quantity, \(B\) the buy-leg VWAP and \(S\) the sell-leg VWAP.

\[
\mathrm{BuyNotional}=QB, \quad \mathrm{SellNotional}=QS
\]

\[
\mathrm{GrossPnL}=Q(S-B)
\]

\[
\mathrm{GrossSpread}_{bps}=\frac{\mathrm{GrossPnL}}{\mathrm{BuyNotional}}\times 10{,}000
\]

For the example above, gross PnL is \(2(104-101)=6\) quote units and gross spread is \(6/202\times10{,}000=297.0297\) bps.

## 4. Trading fees and net PnL

Let \(f_b\) and \(f_s\) be the buy-venue and sell-venue fee fractions:

\[
\mathrm{BuyFee}=\mathrm{BuyNotional}\times f_b
\]
\[
\mathrm{SellFee}=\mathrm{SellNotional}\times f_s
\]
\[
\mathrm{NetPnL}=\mathrm{GrossPnL}-\mathrm{BuyFee}-\mathrm{SellFee}
\]
\[
\mathrm{NetSpread}_{bps}=\frac{\mathrm{NetPnL}}{\mathrm{BuyNotional}}\times10{,}000
\]

Using \(f_b=f_s=0.001\), example fees are \(202\times0.001=0.202\) and \(208\times0.001=0.208\). Net PnL is \(6-0.202-0.208=5.59\) and net spread is approximately \(276.7327\) bps.

The scanner emits a candidate only when net spread is **strictly greater** than the configured threshold. Zero-profit candidates do not pass a zero-bps threshold.

## 5. Symbol trading constraints

Rules are exchange- and symbol-specific and must be supplied explicitly; Arb Foundry does not guess tick sizes or minimums.

- Price tick: permitted price must be an integer multiple of price_tick.
- Quantity step: permitted quantity must be an integer multiple of quantity_step.
- Minimum quantity: \(Q \ge Q_{min}\).
- Maximum quantity (when configured): \(Q \le Q_{max}\).
- Minimum quote notional: \(P\times Q \ge N_{min}\).

Quantity rounding is downward:

\[
Q_{rounded}=\left\lfloor\frac{Q}{\mathrm{step}}\right\rfloor\times\mathrm{step}
\]

For marketable limit-order simulation, price rounding is conservative: buys round up to the next tick; sells round down. The resulting price must still pass all configured filters. Real exchange filters may depend on order type and can change; validate current official market metadata before interpreting a paper result.

## 6. Slippage in the paper simulator

A configured slippage assumption \(s\) in bps is applied adversely to each leg's order-book VWAP:

\[
r=\frac{s}{10{,}000},\quad B'=B(1+r),\quad S'=S(1-r)
\]

The simulated buy pays \(QB'\), while the simulated sell receives \(QS'\). Fees are computed on these adjusted quote notionals.

\[
\mathrm{GrossPnL}_{slipped}=Q(S'-B')
\]
\[
\mathrm{NetPnL}_{paper}=\mathrm{GrossPnL}_{slipped}-(QB'f_b)-(QS'f_s)
\]

The estimated slippage cost relative to raw book VWAPs is:

\[
\mathrm{SlippageCost}=Q(B+S)r
\]

This is a simple stress assumption, not a calibrated market-impact model. It does not simulate queue priority, latency races, changing books, exchange outages, transfer/withdrawal costs, rebates, taxes, or probability that both legs fill.

## 7. Partial fills and paired quantity

When partial fills are disabled, the simulation rejects the trade if either book cannot cover the requested quantity; no PnL is recorded for that rejected attempt.

When partial fills are enabled, the paired simulated quantity is:

\[
Q_{paired}=\min(Q_{requested}, Q_{buy\ depth}, Q_{sell\ depth})
\]

Both legs are then priced for that same paired quantity. The remainder is reported as unfilled; the simulator does not silently treat it as executed.

## 8. What these figures do not mean

- Scanner spread is an estimate from a snapshot, not realized return.
- Fees are assumptions unless verified for the relevant account, market, and fee tier.
- The paper simulator is deterministic and does not send orders or maintain exchange balances.
- Market constraints are a validation utility and are not yet automatically sourced from exchange metadata or wired into the scanner.
- A future production-quality evaluation should add timestamp-skew checks between venues, explicit inventory and transfer costs, durable trade/event records, latency and fill-probability models, and risk limits.
