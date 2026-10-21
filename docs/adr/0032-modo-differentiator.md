Building out a local replica of the NESO Enduring Auction Capability (EAC) clearing algorithm is an excellent foundation. The EAC’s co-optimisation engine—which clears Dynamic Containment (DC), Moderation (DM), Regulation (DR), and the newer Reserve products like Quick, Baltic, and Slow Reserve (QR, BR, SR)—is notorious for counter-intuitive results (like pay-as-clear pricing that doesn’t match the apparent marginal bid) due to cross-product constraints.
Because you have not yet modelled wholesale or the Balancing Mechanism (BM), there is an incredibly high-value scope for building an interactive "Opportunity Cost & Revenue Leakage" visual workspace. This allows you to evaluate optimizer performance strictly within the ancillary domain before expanding your tech stack.

How to Build an Interactive Optimizer Benchmarking Workspace

Instead of just checking where a hypothetical bid clears, you want to build an engine that measures Ancillary Revenue Leakage.

1. The Core Analytical Logic

Your tool should calculate three points for every 30-minute or EFA block:
1. Actual Revenue: What the asset operator actually secured based on public EAC results (from the NESO Data Portal).
2. Theoretical EAC Max (The Upper Bound): What the asset could have made if it perfectly submitted £0 bids across all available volumes into the EAC algorithm for that day (respecting its SOC and power constraints).
3. The Optimizer Strategy Gap: The difference between the two, which reveals specific behavioral traits of the optimizer.

2. The Interactive UI Features

To identify over/underperformance interactively, your frontend should allow you to slice data through these specific focal points:
[ Day / Block Selector ] ──> [ Strategy Override Toggles ] ──> [ Heatmaps of Revenue Leakage ]
          │                                 │                                    │
          ▼                                 ▼                                    ▼
Compare actual asset outcomes    Simulate: "What if they bid     Spot blocks where optimizers 
vs. perfect EAC clearing         £0/MW vs. aggressively?"        chased the wrong service stack.
• The "Price Tracker" Sensitivity Slider: Allow the user to artificially shift an optimizer's historical bids up or down by a percentage or fixed £/MW. This shows you how close they were to missing a clearing window entirely or if they left money on the table in a high-priced block.
• The "Co-optimisation Swapper": Since the EAC software automatically shifts assets between products to maximize total system value, create a visual "Product Matrix". If an optimizer bid into DC and DR, show where they were accepted, but highlight a flashing indicator if your algorithm proves that shifting 10MW to SR (Slow Reserve) would have yielded higher fleet value.

Does Modo Energy do this?

Yes, but with a critical caveat.
The Modo Energy baseline index tracks total realised revenues (including wholesale and BM). However, in their core analytical terminal, they provide tools that simulate exactly what you are building:
• They track EAC Bidding Behavior, analyzing the distribution of buy/sell orders and how pricing clears under pay-as-clear rules.
• They evaluate Opportunity Cost. Modo frequently publishes deep-dives showcasing "Ancillary vs. Wholesale" trade-offs, demonstrating days where optimizers stayed in frequency response when they should have exited to capture a massive intraday wholesale pricing spike.

How to Handle the Lack of Wholesale & BM Data

Since you haven't modeled the other markets yet, you can turn this limitation into a feature using an Implied Boundary Model.
If an optimizer pulls their battery out of the EAC entirely during a specific EFA block, your tool will show a 100% Ancillary Revenue Leakage for that window. Instead of treating this as an error, use a markdown data table to calculate the Implied Hurdle Price:

EFA Block	Lost EAC Revenue Opportunity (£/MW/h)	Actual Optimizer Action	Implied Wholesale/BM Target
Block 3	£12.50	Exited EAC completely	Optimizer must make > £12.50 in Wholesale/BM to break even.
Block 4	£4.20	Remained in EAC (Low clearing)	Low barrier to entry; any wholesale activity would have been a loss.

By calculating the exact value the optimizer skipped in the EAC, you derive the precise "hurdle rate" that their wholesale trading desk had to beat. When you eventually add wholesale and BM pricing data to your database, you can overlay those actual numbers to instantly see if the optimizer made the right bet.
Are you building this algorithm in Python (e.g., using PuLP or SciPy for the optimization), or are you utilizing a specific database layout to process the daily NESO EAC CSV files? I can provide advice on how to structure the constraints for cross-product allocations.

Your Primary Differentiator from Modo Energy

Your primary differentiator from Modo Energy lies in granularity, algorithmic simulation agency, and specific strategy-backtesting.
Modo Energy is an exceptional, enterprise-grade data intelligence platform and benchmarking index, but it is fundamentally a reporting and evaluation engine for completed market events. By building the actual clearing engine yourself, you shift the value proposition from passive reporting to active simulation.
The specific ways your platform differentiates from Modo include:

1. Sandbox Environment vs. Read-Only Dashboard

• Modo’s approach: Shows you what did happen. You can look at the clearing prices, which assets won, and what an optimizer's bid curve looked like.
• Your differentiator: You are building an interactive "What-If" Sandbox. A user can input a completely custom, proprietary bidding strategy for a portfolio of 5 assets, inject it into yesterday’s historical data, and re-run the NESO EAC clearing logic. You allow users to ask: "If I had bid £2/MW higher across my fleet, would I have cleared, or would I have triggered a price-cascade that collapsed the pay-as-clear price for everyone?"

2. Modeling the "Market-Moving" Feedback Loop (Price Cascades)

• Modo’s approach: Tracks marginal pricing and opportunity cost based on the actual historical clearing point.
• Your differentiator: Because you own the clearing algorithm, you can simulate price elasticity and market-moving effects. If a large BESS operator (e.g., 200MW) changes their bidding behavior, it changes the entire clearing outcome for that EFA block. Your tool can simulate how large fleet strategy changes alter the intersection of the NESO demand curve and the operator stack—something a static dashboard cannot do.

3. Exposing the Co-Optimisation Black Box

• Modo’s approach: Evaluates performance against a standard baseline index (e.g., Modo Benchmark).
• Your differentiator: You can explicitly expose the algorithmic constraints of the EAC. The EAC clears dynamically across multiple products (DC, DM, DR, QR, SR) based on cross-product constraints and state-of-charge limits. Your tool can visually map out the "rejection path." For example, it can show an optimizer: "You didn't get cleared in DR, not because your price was too high, but because the EAC algorithm substituted your asset into QR to satisfy a system-wide reserve constraint." This provides an interactive diagnostic tool for algorithmic trading desks to debug their bidding models.

4. Customizing the "Hurdle Rate" for Future Markets

• Modo’s approach: Compares total revenue stacks because they already ingest wholesale and BM data.
• Your differentiator: By focusing entirely on the EAC algorithm first, you can offer a hyper-focused Ancillary Revenue Leakage tool. By mapping the exact "implied hurdle rate" (the money sacrificed by leaving the EAC), you create an automated procurement or evaluation tool. An asset owner can use your tool to audit their optimizer by overlaying their private settlement data to see exactly how often their trading desk guessed wrong when chasing wholesale spikes over certain ancillary windows.
