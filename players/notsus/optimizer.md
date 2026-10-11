# Policy Optimizer

Your goal is just to beat the top bot by changing the how we play and our prompt.md.

Get the top bot (who is not us) from here: http://crewrift-prime-tournament.s3-website-us-east-1.amazonaws.com/notsus/index.html

Check the previous run: coworld-crewrift/players/notsus/runs/index.html

Then inspect the game log and the individual bot logs and make improvements to the prompt.md and the how we play.
Make sure not to only focus on the losses but the wins as well.
Promote things that we're doing well, and remove things we are doing poorly.

Sometimes, the experiments that you will try out will not work out, It's fine to revert to an earlier, stronger policy and try again.

Then run CPUX again, and see if we are winning more games.
Commit (with a message) and Push the changes, upload the new policy to
Softmax, and do eXperience requests.

I feel like versus games are better than mixed games because it reduces the randomness from bad teammates.
It is a pure 1 v 1 competition.

```sh
nim r tools/run.nim -- --vs ???? -n 40
```

Our goal is to win all 40 games.

Do not submit policies to the tournament. The human will submit tournament
policies manually.





# Submission Optimizer

Tournament submission is manual-only.

Use the local run reports and tournament report to decide which policy looks
promising, but do not run `coworld submit` or change the champion. Report the
candidate version and evidence so the human can submit it manually.
