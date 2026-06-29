Your goal is just to beat all of the top three other bots by changing the how we play and our prompt.md.

Get the top bots (who are not us) from here: http://crewrift-prime-tournament.s3-website-us-east-1.amazonaws.com/notsus/index.html

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

If you feel like our policy is significantly better than what we submitted recently,
submit this policy as the champion to the tournament (do not wait for qualification it takes too long).

Our goal is to win all 40 games.
