# The model was the easy part

*Notes from building energia-forecast, a day-ahead electricity forecasting system that runs
on free tiers and grades itself in public. Canonical version:
https://diogogs.github.io/posts/the-model-was-the-easy-part/ · July 2026*

[energia-forecast](https://github.com/diogogs/energia-forecast) is a system that wakes up
every morning, looks at what happened in the Portuguese power grid, and predicts tomorrow.
Two predictions, actually. National electricity demand, hour by hour, and the day-ahead
price on MIBEL, the Iberian electricity market. It publishes both before the market's noon
auction, stores them in a table that can never be edited, and then scores itself against
reality, where anyone can check.

There's a [live dashboard](https://energia-forecast-bwwhirmyetaphmsk84dkqg.streamlit.app/),
a [read-only API](https://energia-forecast-api.onrender.com/docs), and the code is public.
I built it in four days. I'll get back to how, because that number deserves an explanation.

The rule I gave myself at the start ended up shaping everything: this had to be a system,
not a notebook. Real data arriving daily, with all its delays and revisions. A hard
deadline, because the auction closes at 12:00 CET whether I'm ready or not. Zero budget.
And no cherry-picking: every forecast gets recorded, including the bad ones.

The model turned out to be the easy part. Almost everything I'm proud of in this project
lives in what surrounds it.

## Why energy

I wanted a domain where the data is public, arrives every day, and means something. The
Portuguese grid delivers on all three: REN publishes consumption and generation at
15-minute resolution, OMIE publishes market prices, Open-Meteo keeps an archive of past
weather forecasts. All free, none of it behind a token.

It's also just an interesting dataset to live inside. At some point I queried the minimum
consumption value in my database and got 87.6 MW, which made no sense for a country whose
quietest night still needs about 3,600 MW. Then I looked at the date. April 28th, 2025.
The Iberian blackout is right there in the data, a day of near-zeros sitting between two
ordinary Mondays. Real data carries history in a way toy datasets never do.

## Backtests lie by default

The thing I organized the whole project around: when you backtest a forecasting model,
it's very easy to accidentally use information that existed for a past date but had not
been *published* yet at the moment you would have forecast. The backtest looks great. The
production system, which doesn't have a time machine, can't reproduce it.

The clearest example is the demand lag. My forecasts go out at 07:00 UTC for the following
day. Feed the model "demand 24 hours before the target" and most of those values fall
inside the current day, which has barely started at forecast time. In a backtest that data
is sitting right there in the database, so the model uses it and looks brilliant. In
production it doesn't exist yet. So demand lags start at 48 hours, and the 7-day lag does
most of the work anyway.

Prices are the opposite case. Day-ahead prices for tomorrow get published today around
13:00 CET, so by the time I forecast, yesterday's auction is old news and a 24-hour price
lag is perfectly legal. Getting these rules right, source by source, is the difference
between a backtest that means something and a nice-looking lie.

Weather was the subtlest one. The obvious move is to train on observed weather. But in
production the model receives a weather *forecast*, not the truth, so I train on archived
forecasts from a pinned ECMWF model: what the forecast said at the time. Same information
in training and in production, no skew.

None of this is enforced by good intentions. Every feature reads data through a single
access layer with an explicit 07:00 cutoff. Ingestion records a first-seen timestamp that
never gets updated. And the CI has leakage tests that fail the build if any feature can see
past the cutoff. I don't trust myself to remember the rules at 11 pm. The tests remember.

## Two dumb rules as referees

Before any machine learning, I wired up two baselines: "tomorrow repeats the most recent
legally usable day" and "tomorrow repeats last week". They run through the same feature
pipeline and land in the same tables as the real model, so the comparison is honest. The
rule is simple. If a model doesn't beat both on the same folds, it doesn't ship.

This sounds obvious and almost nobody does it, because naive baselines in power systems are
embarrassingly strong. Weekly seasonality alone gets you to about 6% error on demand.

The model that beats them is deliberately boring: a single LightGBM, retrained from scratch
every morning in a few seconds, fed calendar features, the legal lags and forecast weather.
On a 71-day rolling-origin backtest it lands at 2.77% MAPE against the seasonal baseline's
5.95%. Roughly half the error. No deep learning anywhere; on tabular data this size, with
daily retraining, it would cost me complexity and buy me nothing.

## Price is a different animal

Demand is a creature of habit. People wake up, factories open, dinner gets cooked. Price
has none of that decency: it goes negative on sunny spring afternoons when solar floods the
market, spikes when the wind dies at the wrong hour, and changes regime whenever gas or
weather decide to. Same pipeline, completely different problem.

Two consequences. First, MAPE, the metric everyone reaches for, is useless here. With
prices crossing zero, percentage errors explode or lose meaning, so the price model is
judged on absolute error and pinball loss instead. Second, a single number felt almost
dishonest for something this volatile, so the model is actually three: quantile regressors
for P10, P50 and P90. Instead of "tomorrow averages 117 €/MWh" you get "eight days out of
ten, it should land between 92 and 139".

Getting that band to tell the truth took two humbling lessons. My first configuration, with
deeper trees, produced a band that looked impressively tight and covered 48% of outcomes
while claiming 80. Textbook overconfidence. Making the models shallower and heavily
regularised improved the point error *and* the coverage at the same time, which tells you
exactly how overfit version one was. Then I added conformal calibration, a technique that
widens or narrows the band based on how wrong it's been recently, and coverage climbed
to 76%.

Not 80. And it won't get there with this method, because price regimes shift faster than
any trailing calibration window can adapt. I could have left that number off the dashboard.
It's on it, with the explanation, because a forecasting product that hides its own
miscalibration is exactly the thing this project exists not to be.

For the record: the P50 beats the persistence baseline, 13.2 €/MWh of mean error against
16.0. Not a huge margin. For day-ahead prices, beating persistence at all is the
meaningful bar.

## Production is where the stories are

Everything above was designed. What follows was not, and it's the part I'd tell in an
interview.

**The library everyone uses for OMIE files corrupts them.** On day one I ran verification
spikes against every data source before committing to anything. Good thing. The community
Python library for OMIE price files, when given a file from after the market switched to
15-minute intervals, silently relabels the first 25 quarter-hours as "hours 1 to 25" and
drops the remaining 71 values. No error, no warning. For a time-series project that is the
worst possible failure mode. I wrote my own parser instead, about forty lines plus tests
against real files from every daylight-saving edge case.

**The grid operator publishes half-days.** REN returns the current day as a truncated
array while it's still in progress. My parser was strict about slot counts, treated the
truncation as corruption, and so the daily ingestion "failed" every day, but only for
today's data. I only noticed the pattern hours after adding a persistent data-quality log.
Observability didn't help debug the problem. It's what made the problem visible at all.

**Free tiers move under your feet.** The database's free 512 MB filled up mostly with
indexes I didn't need, so out they went. My deployment plan said Hugging Face Spaces right
up until the week I deployed, when Docker Spaces turned out to have moved behind a paid
plan. Pivoted to Render the same afternoon. The architecture didn't care, because all
state lives in the database and serving is stateless.

**GitHub Actions cron is not a scheduler you can trust.** This one deserves its own post.
The docs say scheduled workflows "can be delayed during periods of high load". What I
measured was every daily event delayed by three hours and twenty minutes, two days in a
row, and about 90% of a twelve-minute schedule silently dropped. My forecast missed the
market auction while every workflow reported success. The fix: time-critical triggers now
come from an external scheduler calling the GitHub API, which starts workflows in seconds,
with the old cron entries kept as a late-but-present fallback. The failure never corrupted
anything, and that part was by construction: ingestion is idempotent and the forecast table
is insert-only, so duplicate or delayed runs are harmless. The system had even flagged its
own late forecasts and excluded them from the headline record. Exactly what I designed it
to do, and still slightly surprising to watch happen.

## About the AI

If you look at the commit history you'll see Claude on every commit. I built this with
AI-assisted development, most lines were typed by Claude Code, and I'd rather be upfront
about that than have you discover it in the git log.

What stayed mine: the charter and the non-negotiable temporal rules, every architecture
decision (there are thirteen ADRs in the repo), the verification of each stage against
live data, and the "no"s. No model ships without beating the baselines. No chart ever
re-predicts the past. No forecast is ever edited. The AI is very good at producing code,
and remarkably willing to produce it for a design that's wrong. The design and the review
are where I earned my keep.

It doesn't remove the need to understand your system. It moves the understanding up a
level. The failure mode isn't the AI writing bad code, it's you accepting code you can't
defend. I can defend this system line by line precisely because reviewing it, testing it
and trying to break it was my actual job for those four days.

## What's next

The system adds a row to its own report card every morning at 07:05 UTC. Next up is drift
monitoring, and that one I'm writing by hand. The rest is patience: letting the live track
record grow long enough to be worth reading.

If you want to poke at it:
[dashboard](https://energia-forecast-bwwhirmyetaphmsk84dkqg.streamlit.app/) ·
[API](https://energia-forecast-api.onrender.com/docs) ·
[source and ADRs](https://github.com/diogogs/energia-forecast).
