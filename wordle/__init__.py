from .wordle import Wordle

async def setup(bot):
    await bot.add_cog(Wordle(bot))
