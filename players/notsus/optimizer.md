# Policy Optimizer

Your goal is just to beat the top bot by changing the how we play and our prompt.md.

Get the top bot (who is not us) from here: http://crewrift-prime-tournament.s3-website-us-east-1.amazonaws.com/notsus/index.html

Check the previous run: coworld-crewrift/players/notsus/runs/index.html

Then inspect the game log and the individual bot logs and make improvements to the prompt.md and the how we play.
Make sure not to only focus on the losses but the wins as well.
Promote things that we're doing well, and remove things we are doing poorly.

Sometimes, the experiments that you will try out will not work out, It's fine to revert to an earlier, stronger policy and try again.

Then run CPUX again, and see if we are winning more games.
Commit (with a message) and Push the changes, Upload the new policy to the tournament and do eXperience requests.

I feel like versus games are better than mixed games because it reduces the randomness from bad teammates.
It is a pure 1 v 1 competition.

```sh
nim r tools/run.nim -- --vs ???? -n 40
```

Our goal is to win all 40 games.

If you feel like our policy is significantly better than what we submitted recently,
submit this policy as the champion to the tournament (do not wait for qualification it takes too long).





# Submission Optimizer

Schedule task every 30 min: Try to submit the best version of NotSus to Andre von Houck.

What we're going to do is we're going to look at the bots that have run in the past and who have high win rates.

coworld-crewrift/players/notsus/runs/index.html

And what we're going to do is we're going to pick a promising bot, and then we're going to submit it to the tournament and make it the main champion.
Then, for several hours, we watch how it is doing in the main tournament.

coworld-crewrift/players/notsus/tournament/index.html

To wait until it gets through the qualification pass, and then it'll receive some small number of games. Every hour it'll play more and more and more games until it reaches a critical mass.
Let's call a hundred games the critical mass for now. True win rate: we're going to be always thinking about win rate. The true win rate is the number of games after 100 games. Before we played a hundred games, it's really hard to tell what their win rate is going to be.

There's some relationship between the runs win rate and the tournament win rate.Tournament win rate is way, way less than the runs win rate.In the run, we usually are playing a one-way-one game where we are trying to beat a single policy, and that's a good signal for optimization, but that's not necessary for the tournament.In tournament, we want to keep the highest winning robot in play.

Might be the fact that we see a version that plays really well in the runs, but when we put it in the tournament, it does really poorly.
Likewise, you might see a version that didn't do that well during the runs but does really well in the tournament.
After about a hundred runs, we should switch to a different policy. We should always be testing a different policy in the tournament to see which one has the highest win rate.
The highest win rate might also change over time. We submitted a policy, and it had a high win rate in the past, but we're going to be submitting again in the future or right now, and it actually doesn't do so well.
You have to make a theory about what's working and what's not.
It's a higher chance that the policies that were invented recently will do better because the competition in the past was way worse, so a good policy in the past might be a mediocre policy today.

If you can get those current two files, which version of NotSus will you submit next?

Should we switch to a different policy now? If so please switch and submit the new policy to Andre von Houck.
