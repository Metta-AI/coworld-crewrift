# Notsus - A simple example bot for Crewrift.

Notsus is an example bot that can do a lot of the tasks, can be an imposter, and figure out how to navigate the terrain and find stuff and generally play the game as you would expect. It is not the ultimate bot. It's very stupid. When watching the game, the shortcomings of this bot can be quickly realized. You can start improving the bot by just watching the replays and seeing all the dumb things that it does and implementing some sort of your own strategy.

Run the bot headless:

```sh
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:8080/player?slot=0&token=' \
nim r players/notsus/notsus.nim
```

Run one bot with the visual debugger:

```sh
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:8080/player?slot=0&token=' \
nim r -d:notsusGui players/notsus/notsus.nim
```

The debugger shows the sprite viewport, the decompressed walkability mask, the
current viewport rectangle, player position, visible objects, current goal,
roam goal, A* path, selected path step, input mask, velocity, and stuck state.
It scales the Silky UI from the current Windy backing size each frame, so moving
the window between high-DPI and low-DPI screens keeps the layout readable.

## How the bot works

Notsus can be broadly divided into several categories:

- You have to parse the sprite protocol and send key and text packets to the server.
- Then we have to go through various modes:
  * The initial joining screen.
  * The role screen that shows you as crewmate or imposter.
  * The actual game part, where you walk around and do tasks or kill others as the imposter.
  * The voting part and the chatting screen.
  * The screen that shows you who was actually killed.
- Then it explores the strategies that you would want to use.

## Parsing the sprite protocol

You can find the sprite protocol in the [sprite_v1.md](https://github.com/Metta-AI/bitworld/blob/master/docs/sprite_v1.md) file.

Originally, we had just sent the whole view of the screen and then had the AI parse the pixels and figure out what we called sprite decomposition, but that actually turned out to be pretty slow. It was using almost all of the CPU budget just to parse and generate the screen, so we switched to something that is a sprite protocol, which basically shows you the sprites that are on that screen and that's it. The protocol here, specifically and very importantly, to show very similar to what the human sees, is very important to us. To have very good "what you see is what you get" is very important to us.

## Playing the game

### Starting the game

The first thing we have to do is be able to parse the joining screen. Here's when you see that more players are required. There's almost nothing to do on the screen but wait.

Then we have: once the correct number of players have joined, there's a little countdown timer. Once the countdown timer goes through, you get to see your role. As a crewmate, you actually see all of the other players arrayed. Even the imposters are shown on the screen, so you don't know who is who.

This is very different for the impostors. Impostors only show, by default, the other two impostors, so you need to remember what color the other imposter is.

### Navigating the game

Then everyone starts at the bottom, at the button, and the normal game starts, but our parsing is much more complicated. First, the server sends you several sprites. As an AI player that doesn't have to visualize a sprite, we can ignore most of the sprite data and don't actually parse it, but we need to parse one sprite, the walkability map, and this is what we need to do. The walkability map gives us the areas you can walk on and the ones you can't. It also sends us the viewport. Actually, everything you do not need to know or do much with the navigation part of things. You have the viewport, and you have which sprites are on screen, which is map-relative, so they all start at 0:0 at the very map start. This makes it very easy to know where you are at all points in time, because everything, all sprites, is relative to the 0:0 coordinate of the main map.

Here, it's recommended to use a pathing algorithm to get around through various nooks and crannies. I recommend using [A*](https://en.wikipedia.org/wiki/A*_search_algorithm), but a lot more advanced pathing algorithms can be used, such as pre-computing the paths or using something advanced like [JPS+](https://www.gameaipro.com/GameAIPro2/GameAIPro2_Chapter14_JPS_Plus_An_Extreme_A_Star_Speed_Optimization_for_Static_Uniform_Cost_Grids.pdf).

The players have a little bit of momentum because the information that you send to the server is pressing buttons on the D-pad, such as left or right, but we need to hold them for some amount of time. Since the server sends you frames whenever it wants to and you can press the button and let go of the button whenever you want to, you need to sense there is momentum. You actually have to be very careful about motion here. This is not a grid where one press can equal one move. No. This is way harder because when you press, you might accelerate for a known amount of frames, and then when you let go, you'll decelerate for a known amount of frames. You need a little baby [PID controller](https://en.wikipedia.org/wiki/PID_controller) for your agents to make sure it can navigate properly.

Just like the path and information, you also get locations of all the tasks and all the events and all the room names as part of the initial packet from the server. You should store all that information for later use.

We are ready to play the game. The crewmates should try to complete the tasks as fast as possible. You have a little radar screen on your screen. You can use that radar to pan and guess where the tasks are, but sometimes those guesses are wrong, especially if multiple tasks appear in the same line or they're far away or something else has happened. You might need to update your guesses from time to time. Just going to the closest tasks is actually not a bad idea, but you have to make sure you don't oscillate between the two closest tasks. This has happened sometimes because of the momentum and how the thing moves. It is best to pick a task and stay the course. You can use more advanced algorithms, such as [traveling salesman](https://en.wikipedia.org/wiki/Travelling_salesman_problem), to plan out a more efficient route for the tasks.

### Being a crewmate

A crewmate also sees the other players on screen. It is good to keep track of what they do because you might need that information later. As part of your navigation, it also might be beneficial to stick around with some other crewmates so that they can vouch for you and you can vouch for them during the voting phase. How much time you spent with the other players and how much time you spent doing the tasks is the key to a good strategy.
Impostors basically need to act like crewmates, so this is important for them as well.

### Being an imposter

As an imposter, you want to blend in with the other crewmates. Acting erratically or just waiting for your kill to go down is probably a recipe for disaster. What you want to do is fake tasks and fool the other imposter to vouch for you during the voting phase.

As an imposter, once your cooldown is down, this becomes really important. You want to kill other players as soon as possible, but not put yourself in an unadvantageous position. First off, it is probably beneficial to isolate a single crewmate so you can perform the kill without anyone seeing it.
It is also very helpful if you have two crewmates waiting for the kill to happen.
You want the kill to be clean, with no one else watching.
You want to kill your victim and then you want to move out of there as far as possible from the kill.
You don't want to be implicated in the murder.
Sometimes you might want to self-report or report the body and blame other players, but you don't want to do that all the time, or that will make you look suspicious. This is where randomness is important.

### Voting as a crewmate

Once a body is found and is reported, or someone just presses the emergency button, the voting process starts.
It is extremely important to be able to parse the voting screen and be able to navigate around the voting screen using the d-pad and selecting the correct thing that you want to vote for.

Then it is also important, if you report the body, to say where the body is. Since you get the room information from the very beginning, you can use that as a navigational aid: "Body found around hydroponics".
Talking is extremely important. Players who do not talk appear incredibly suspicious.
As a crewmate, you should probably clear other players, saying, "I was with Red."

As a crewmate, it's very dangerous to say someone was sus because, for crewmates, voting out a crewmate is an extremely huge blow to win the game. You really want to be absolutely sure that someone is sus. The reasons why you might want to see that someone is sus are:
- You saw them next to the body and they did not report the body, which is a very big sus behavior.
- You saw someone vent, which basically proves that they are an imposter. You should say those kinds of things.

As a crewmate, it's important to find someone in a logical contradiction. When someone says, "I was in the MedBay, I was doing tasks" but they weren't there and "I was in the MedBay, but green was not there", that probably means an imposter. When someone says they were at this position, but that position is far away from where someone else saw them, that might mean venting, which is an imposter type behavior.

Sometimes impostors just hang around, moving erratically from left to right, trying to find a good victim or a good spot to be not doing tasks. That is also an imposter style behavior and should be sussed out.

### Voting as an imposter

As an imposter, you have several different strategies.

A passive strategy is to wait for when the other crewmates are suspicious of someone and also add something to the fire.
* You can wait for when the other crewmates are suspicious of someone and also add something to the fire.
* Both of the imposters should see who the other imposter is voting for and vote for that person as well.
* The imposters should probably stick to other crewmates and clear them.
* They might also want to clear each other.

An active strategy might involve accusing someone right away:
* "I saw yellow at the body. They did it!"
* Have the other imposter go along with it. "Yeah, the yellow was acting suspicious, not doing the task."
* If the other crewmates say something to clear yellow, try to deflect blame, try to cause chaos.

Another sneaky strategy could be "self-reporting", where you kill someone and report the body right away:
* And then you go, "Who did it? I found the body."
* Play dumb. "I don't know anything, I was just doing the task."
* By self-reporting, you clear the body of the map, so it could be a useful strategy.
* But by self-reporting all the time, you will develop a pattern, and that's not good either.

## Voting as a Large Language Model

You might choose to hook up an LLM to your voting phase. Basically, what you need to do is provide all the information that has been collected: where, who, why, for what, and what you saw. You provide that as a context to the LLM. Then you tell the LLM to try to deduce, or at least say something that will allow it to deduce, who are the impostors, and then do the rounds of this. The voting timer is quite short, so it's recommended to use a much faster LLM.

It's also quite hard to make the LLM play the game and not cosplay the game. You might have to have a quite clever system prompt, but LLMs really don't want to play the game. Instead, they want to make up things and accuse things and do things that they think a player should do, but something that is actually not beneficial to do at the moment.

### Metta Strategies

Crewmates can sort of "exploit the game" by hitting the emergency button to reset the Impostor cooldown. The Impostor Cooldown is a bit short, but this can seriously disrupt their behavior. Every crewmate needs to agree to press the emergency button to reset the cooldown. If a bunch of crewmates are doing this, the impostors need to do this as well to blend in. Eventually, pressing the emergency button, because you can only press it once, will go away, and then impostors can kill everyone else because the crewmates were not doing the tasks and they were just focusing on the button.

Because crewmates can see when tasks are getting completed, an imposter might come to the task and then stand there like they are doing the task, but then they leave if the task counter did not change. This means they didn't do the task because they can't. This is very sus behavior and should be reported right away.

It might be beneficial for all crewmates to move as one large group and then all just move around the ship, waiting for everyone to do the tasks in that section. Then, if someone is an imposter and kills someone, all the crewmates will immediately know who that is. Crewmates have to be careful, though, because the game has a limited timer. They need to do this quite quickly in order to accomplish a win, and they need to be quite well organized.

Right in the very beginning, one of the crewmates can call the emergency button, and that sets some weird rules for the game to follow. We're all going to do strategy X or strategy Y. "We're all going to watch everyone do one task, and then once someone is unable to do a task, we know that this is an imposter and we can vote them out."

There are a bunch of strategies in the game that can work. The hard part is making sure that when you are together playing with eight AI bots that have nothing to do with each other (possibly written by different people in different languages), they all can somehow figure out and follow one of the meta strategies.

## Source Tour

Use the source as the real guide for writing a bot. Every useful bot has to
solve the same chain of problems: connect to the player websocket, understand
the sprite protocol, recover the map and walkability, work out who it is,
choose goals, move to them, act, talk, vote, and exit cleanly. In this bot, the
low-level protocol code is in `notsus/protocols.nim`, while most strategy code
is in `notsus.nim`.

- To connect to the game and run the frame loop, start at `runBot`.
- To parse Sprite v1 and send input or chat, read `receiveLatestFrameInto`,
  `applySpritePacket`, `inputBlob`, and `chatBlob` in `notsus/protocols.nim`.
- To extract task, room, vent, camera, player, body, vote, and chat objects
  from sprite labels, read `updateProtocolDetections`.
- To build the static game map from protocol markers, read
  `updateProtocolMap` and `applyProtocolMap`.
- To load the walkability map before pathing, read the walkability branch in
  `updateProtocolDetections`.
- To work out whether the bot is a crewmate, imposter, or ghost, read
  `rememberRoleReveal`, `updateRole`, `updateSelfColor`, and the "IMPS" branch
  in `updateProtocolDetections`.
- To keep track of where the bot is on the map, read `updateLocation`.
- To choose the next action each frame, read `decideNextMask`.
- To pick tasks and complete them, read `nearestTaskGoal` and
  `holdTaskAction`.
- To navigate through the ship, read `findPath`, `navigateToPoint`,
  `axisMask`, and `preciseAxisMask`.
- To detect and report bodies, read `scanBodies`, `queueBodySeen`,
  `queueBodyReport`, and `reportBodyAction`.
- To pretend to be a crewmate as an imposter, then hunt later, read
  `decideImposterMask`.
- To press the emergency button for cooldown resets, read
  `buttonResetShouldAct` and `pressButtonResetAction`.
- To parse voting, choose a vote, and send meeting chat, read
  `parseVotingScreen`, `desiredVotingDecision`, `decideVotingMask`, and
  `pendingChatReady`.

The fastest way to improve the bot is to watch a replay, name the failed
capability, jump to the function above, make one small change, and run the bot
again.
