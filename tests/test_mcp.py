import asyncio
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
import sys

def test_actual_stdio_protocol(tmp_path):
    config=tmp_path/'config.toml'
    config.write_text('database = "'+str(tmp_path/'mcp.sqlite3')+'"\n')
    async def run():
        params=StdioServerParameters(command=sys.executable,args=['-m','blixscraper.cli','--config',str(config),'serve'])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                result=await client.initialize()
                assert result.serverInfo.name=='Blix shopping assistant'
                tools=(await client.list_tools()).tools
                assert {t.name for t in tools}=={'data_status','search_offers','get_offer','compare_basket'}
                assert all(t.annotations.readOnlyHint for t in tools)
                for name,args in [('data_status',{}),('search_offers',{'query':'молоко'}),
                                  ('compare_basket',{'items':[{'query':'mleko','quantity':2,'unit':'l'}]} )]:
                    r=await client.call_tool(name,args)
                    assert not r.isError
                r=await client.call_tool('get_offer',{'offer_id':'missing'})
                assert r.isError
                r=await client.call_tool('search_offers',{'query':'x','stores':['../../etc']})
                assert r.isError
    asyncio.run(run())
