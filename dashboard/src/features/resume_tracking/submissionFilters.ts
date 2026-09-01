import type { FilterFieldConfig, FilterValues, SortOption } from '../../components/FilterSortBar'
export const submissionFilterFields=(bucket:string,includeOpportunityFields=true):FilterFieldConfig[]=>[
  {key:'q',label:'Search',type:'text'},
  {key:'company',label:'Company',type:'combobox',bucket},
  {key:'recruiter',label:'Recruiter',type:'combobox',bucket},
  {key:'role',label:'Job title',type:'combobox',bucket},
  ...(includeOpportunityFields?[{key:'opportunity_domain',label:'Domain',type:'text'} as FilterFieldConfig,{key:'implementation_partner',label:'Implementation Partner',type:'combobox',bucket} as FilterFieldConfig]:[]),
  {key:'end_client',label:'End Client',type:'combobox',bucket},
  {key:'has_premium_contact',label:'In Premium Numbers',type:'boolean'},
  {key:'tracked',label:'Tracked (has opportunity)',type:'boolean'},
]
export const submissionSortOptions:SortOption[]=[{value:'newest',label:'Newest first'},{value:'oldest',label:'Oldest first'},{value:'next_action',label:'Next action due'}]
export const submissionDefaultFilterValues:FilterValues={q:'',company:'',recruiter:'',role:'',opportunity_domain:'',implementation_partner:'',end_client:'',has_premium_contact:null,tracked:null}
export function submissionFiltersToParams(v:FilterValues){const p:Record<string,string>={};for(const key of ['q','company','recruiter','role','opportunity_domain','implementation_partner','end_client']){const value=v[key] as string;if(value?.trim())p[key]=value.trim()}if(v.has_premium_contact!=null)p.has_premium_contact=String(v.has_premium_contact);if(v.tracked!=null)p.tracked=String(v.tracked);return p}
