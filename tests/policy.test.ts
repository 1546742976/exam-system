import { expect,it } from 'vitest';
import { mayManage,validMappings } from '../addon/handlers/policy';
it('only domain editor or authorized owner manages contest',()=>{
  expect(mayManage({id:2,edit:false,editSelf:true},{owner:1})).toBe(false);
  expect(mayManage({id:1,edit:false,editSelf:true},{owner:1})).toBe(true);
  expect(mayManage({id:1,edit:false,editSelf:false},{owner:1})).toBe(false);
  expect(mayManage({id:2,edit:true,editSelf:false},{owner:1})).toBe(true);
});
it('rejects arbitrary target and duplicate assignments',()=>{
  const registry=new Set(['a']);
  expect(()=>validMappings([{studentId:1,deviceId:'https://untrusted'}],registry)).toThrow();
  expect(()=>validMappings([{studentId:1,deviceId:'a'},{studentId:2,deviceId:'a'}],registry)).toThrow();
  expect(validMappings([{studentId:1,deviceId:'a',origin:'ignored'}],registry)).toEqual([{studentId:1,deviceId:'a'}]);
});
