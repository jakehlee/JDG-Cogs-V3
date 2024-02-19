from .vlr import VLR

async def setup(bot):
    await bot.add_cog(VLR(bot))
