import asyncio
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
import sys
import json

def test_actual_stdio_protocol(tmp_path):
    config=tmp_path/'config.toml'
    config.write_text('database = "'+str(tmp_path/'mcp.sqlite3')+'"\nauto_refresh = false\n')
    async def run():
        params=StdioServerParameters(command=sys.executable,args=['-m','blixscraper.cli','--config',str(config),'serve'])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                result=await client.initialize()
                assert result.serverInfo.name=='Blix shopping assistant'
                tools=(await client.list_tools()).tools
                assert {t.name for t in tools}=={'data_status','search_offers','get_offer','compare_basket','refresh_offers','search_history','add_receipt','search_purchases','delete_receipt'}
                assert all(t.annotations.readOnlyHint for t in tools if t.name not in {'refresh_offers','add_receipt','delete_receipt'})
                assert not next(t for t in tools if t.name=='refresh_offers').annotations.readOnlyHint
                for name,args in [('data_status',{}),('search_history',{'query':'яйца','since':'2026-09-01'}),('search_offers',{'query':'молоко'}),
                                  ('compare_basket',{'items':[{'query':'mleko','quantity':2,'unit':'l'}]} )]:
                    r=await client.call_tool(name,args)
                    assert not r.isError
                r=await client.call_tool('add_receipt',{'store':'Aldi','purchased_on':'2026-10-10','items':[{'name':'Jajka','paid_pln':'19.98','quantity':'2','unit':'pack'}],'total_paid_pln':'19.98'})
                assert not r.isError
                r=await client.call_tool('search_purchases',{'query':'jajka'})
                assert not r.isError and json.loads(r.content[0].text)['total_matches']==1
                r=await client.call_tool('get_offer',{'offer_id':'missing'})
                assert r.isError
                r=await client.call_tool('search_offers',{'query':'x','stores':['../../etc']})
                assert r.isError
    asyncio.run(run())
