# Research interest profile (used by Claude to rank papers — edit freely)

I'm a CS PhD student working on robot learning for complex, contact-rich, dexterous manipulation.

Core interests (rank highest):
- Dexterous manipulation: multi-fingered hands, in-hand manipulation, tactile/visuotactile sensing,
  learning from human hand data (mocap, egocentric video, retargeting), sim-to-real for hands.
  Groups I follow: Haozhi Qi, Tyler Lum, Lerrel Pinto, Xiaolong Wang, Deepak Pathak, Karen Liu, Jitendra Malik.
- Imitation learning with generative policies: diffusion policy, flow-matching policy, action chunking
  (why it works, real-time chunking, streaming flow policies), fast/few-step policies (consistency policy),
  long-context / history-conditioned policies, learning from low-quality or corrupted data (ambient diffusion policy).
- RL + generative policies: DPPO, IDQL, DQL, DIPO, QSM, FPO, steering diffusion policies in noise space,
  RL fine-tuning of BC/VLA policies.
- Generative modeling theory and methods: diffusion/flow equivalence, stochastic interpolants, consistency models,
  MeanFlow and one-step generation, guidance (CFG, autoguidance), noise conditioning, energy-based / equilibrium matching.

Also interested in:
- Robot foundation models / VLAs (e.g., TRI LBM, pi0-style models): training recipes, data mixtures,
  fine-tuning (ControlNet-style), fast inference, leveraging VLMs for dexterity.
- World models: unified video-action models, V-JEPA-style pretraining, video world models for policy learning/eval,
  synthetic data from video generation (DreamGen).
- Video generation: diffusion forcing, autoregressive video diffusion.
- Online/offline RL fundamentals and tricks (SAC, PPO, CQL/IQL/Cal-QL, layer norm, TD-MPC2), policy-gradient theory.
- Control for robotics (MPC/MPPI, trajectory optimization, contact-implicit control).

Less interested in: autonomous driving, drones/UAV, pure SLAM/localization, LLM-only agents without embodiment,
medical robotics, unless the method is clearly transferable to manipulation policy learning.

What makes a paper "good" for me: a clear new insight or analysis (not just a new benchmark number),
strong real-robot results, rigorous understanding papers (why X works), or methods I could plausibly use.
- Agentic robotics: LLM/VLM orchestrators that plan, call VLA skills and tools, check success and re-plan; code-as-policy; embodied reasoning models; memory for long-horizon tasks; agents that write rewards, generate tasks or run data collection.
- Tactile sensing: visuotactile/GelSight/DIGIT sensors, tactile skins, force/torque feedback, touch-conditioned policies and tactile representation learning (especially for dexterous hands).
